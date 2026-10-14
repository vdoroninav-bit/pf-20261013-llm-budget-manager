// Типы контракта, которыми пользуется каркас. Источник — сгенерированный schema.d.ts (npm run gen:api).
import type { components } from './schema';

export type Schemas = components['schemas'];
export type Session = Schemas['Session'];
export type AdminUser = Schemas['AdminUser'];
export type Role = Schemas['Role'];
export type Problem = Schemas['Problem'];
