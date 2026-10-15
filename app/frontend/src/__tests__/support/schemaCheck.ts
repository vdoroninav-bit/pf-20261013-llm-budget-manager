// Минимальная проверка значения по JSON Schema из 12_openapi.yaml (OpenAPI 3.1).
// Нужна тестам, чтобы фейковый API принимал только тела запросов по контракту и сам отвечал по контракту.
// Поддержаны конструкции, которые встречаются в контракте: $ref, type (в т.ч. массив с 'null'), const, enum,
// required, properties, additionalProperties: false, items, pattern, minLength/maxLength, minimum/maximum,
// oneOf, allOf. format не проверяется (кроме того, что это строка).

export type Schema = Record<string, unknown>;

export interface SchemaRoot {
  components: { schemas: Record<string, Schema> };
}

const typeOf = (v: unknown): string => {
  if (v === null) return 'null';
  if (Array.isArray(v)) return 'array';
  if (typeof v === 'number') return Number.isInteger(v) ? 'integer' : 'number';
  return typeof v;
};

const typeMatches = (actual: string, expected: string) =>
  actual === expected || (expected === 'number' && actual === 'integer');

export function resolveRef(root: SchemaRoot, schema: Schema): Schema {
  let s = schema;
  while (typeof s.$ref === 'string') {
    const name = s.$ref.split('/').pop()!;
    const next = root.components.schemas[name];
    if (!next) throw new Error(`schemaCheck: нет схемы ${s.$ref}`);
    s = next;
  }
  return s;
}

/** Возвращает список нарушений; пустой список — значение соответствует схеме. */
export function checkSchema(root: SchemaRoot, schemaIn: Schema, value: unknown, path = '$'): string[] {
  const schema = resolveRef(root, schemaIn);
  const errors: string[] = [];
  const t = typeOf(value);

  if ('const' in schema && value !== schema.const) errors.push(`${path}: ожидалось ${JSON.stringify(schema.const)}`);
  if (Array.isArray(schema.enum) && !schema.enum.includes(value)) {
    errors.push(`${path}: ${JSON.stringify(value)} не из ${JSON.stringify(schema.enum)}`);
  }
  if (schema.type !== undefined) {
    const types = Array.isArray(schema.type) ? (schema.type as string[]) : [schema.type as string];
    if (!types.some((x) => typeMatches(t, x))) {
      errors.push(`${path}: тип ${t}, ожидался ${types.join('|')}`);
      return errors;
    }
  }
  if (Array.isArray(schema.oneOf)) {
    const ok = (schema.oneOf as Schema[]).filter((s) => checkSchema(root, s, value, path).length === 0).length;
    if (ok !== 1) errors.push(`${path}: подходит ${ok} вариантов oneOf, нужен ровно 1`);
  }
  if (Array.isArray(schema.allOf)) {
    for (const s of schema.allOf as Schema[]) errors.push(...checkAllOfPart(root, s, value, path));
  }
  if (t === 'string') {
    const s = value as string;
    if (typeof schema.pattern === 'string' && !new RegExp(schema.pattern, 'u').test(s)) {
      errors.push(`${path}: «${s}» не подходит под ${schema.pattern}`);
    }
    if (typeof schema.minLength === 'number' && [...s].length < schema.minLength) errors.push(`${path}: короче ${schema.minLength}`);
    if (typeof schema.maxLength === 'number' && [...s].length > schema.maxLength) errors.push(`${path}: длиннее ${schema.maxLength}`);
  }
  if (t === 'integer' || t === 'number') {
    const n = value as number;
    if (typeof schema.minimum === 'number' && n < schema.minimum) errors.push(`${path}: меньше ${schema.minimum}`);
    if (typeof schema.maximum === 'number' && n > schema.maximum) errors.push(`${path}: больше ${schema.maximum}`);
  }
  if (t === 'array' && schema.items) {
    (value as unknown[]).forEach((item, i) => errors.push(...checkSchema(root, schema.items as Schema, item, `${path}[${i}]`)));
  }
  if (t === 'object') errors.push(...checkObject(root, schema, value as Record<string, unknown>, path, true));
  return errors;
}

// Внутри allOf закрытость (additionalProperties: false) каждой части по отдельности не проверяется:
// части дополняют друг друга (CallerCreated = Caller + api_key).
function checkAllOfPart(root: SchemaRoot, part: Schema, value: unknown, path: string): string[] {
  const s = resolveRef(root, part);
  if (typeOf(value) !== 'object') return checkSchema(root, s, value, path);
  return checkObject(root, s, value as Record<string, unknown>, path, false);
}

function checkObject(root: SchemaRoot, schema: Schema, obj: Record<string, unknown>, path: string, strict: boolean) {
  const errors: string[] = [];
  const props = (schema.properties ?? {}) as Record<string, Schema>;
  for (const req of (schema.required ?? []) as string[]) {
    if (!(req in obj)) errors.push(`${path}.${req}: обязательное поле отсутствует`);
  }
  for (const [k, v] of Object.entries(obj)) {
    if (props[k]) errors.push(...checkSchema(root, props[k], v, `${path}.${k}`));
    else if (strict && schema.additionalProperties === false) errors.push(`${path}.${k}: поле не объявлено в схеме`);
  }
  return errors;
}
