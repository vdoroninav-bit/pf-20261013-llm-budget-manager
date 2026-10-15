// Экран 6 «Загрузка выгрузки групп AD со сводкой» (design/screens.md §6). Только администратор.
// Шаги: выбор файла → проверка (importOrg dry_run=true) → сводка → применение (dry_run=false) → результат.
import { useId, useRef, useState, type DragEvent } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { Title, useDataProvider, useTranslate } from 'react-admin';
import { Link } from 'react-router-dom';
import { can } from '../access/permissions';
import type { Schemas } from '../api/types';
import type { BmDataProvider } from '../dataProvider';
import { ORG_FILE_MAX_BYTES } from '../config/ui';
import { ru } from '../i18n/ru';
import { ConfirmDialog, Forbidden, codeOf, messageOf, statusOf, useRole } from '../components/common';

type Summary = Schemas['OrgImportSummary'];
const t = ru.bm.org_import;

type Step =
  | { kind: 'idle' }
  | { kind: 'checking'; file: File }
  | { kind: 'checked'; file: File; summary: Summary }
  | { kind: 'applying'; file: File; summary: Summary }
  | { kind: 'applied'; summary: Summary };

function checkErrorText(error: unknown): string {
  const code = codeOf(error);
  const detail = (error as { body?: { detail?: string } }).body?.detail;
  if (code === 'file_too_large' || statusOf(error) === 413) return ru.bm.problem.file_too_large;
  if (statusOf(error) === 422 && !detail) return t.bad_file;
  return messageOf(error);
}

const noChanges = (s: Summary) =>
  s.departments_added + s.employees_added + s.employees_updated + s.employees_moved + s.employees_removed === 0;

function SummaryView({ summary, applied }: { summary: Summary; applied: boolean }) {
  const heading = applied ? t.summary_applied : t.summary_dry;
  const rows: Array<[string, number, boolean?]> = [
    [t.rows_total, summary.rows_total],
    [applied ? t.rows_applied : t.rows_applied_dry, summary.rows_applied],
    [t.departments_added, summary.departments_added],
    [t.employees_added, summary.employees_added],
    [t.employees_updated, summary.employees_updated],
    [t.employees_moved, summary.employees_moved],
    [applied ? t.employees_removed : t.employees_removed_dry, summary.employees_removed, summary.employees_removed > 0],
  ];
  return (
    <Box component="section" role="region" aria-label={heading} aria-live="polite" sx={{ mt: 2 }}>
      <Typography component="h2" variant="h6">
        {heading}
      </Typography>
      {noChanges(summary) && (
        <Alert severity="info" sx={{ my: 1 }}>
          {t.no_changes}
        </Alert>
      )}
      <Table size="small" sx={{ maxWidth: 560 }}>
        <TableBody>
          {rows.map(([label, value, warn]) => (
            <TableRow key={label}>
              <TableCell component="th" scope="row">
                {label}
              </TableCell>
              <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums', color: warn ? 'warning.main' : undefined }}>
                {value}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
        {t.rule}
      </Typography>
      <RowMessages title={t.errors} column={t.problem} items={summary.errors} severity="error" />
      <RowMessages title={t.warnings} column={t.warning} items={summary.warnings} severity="warning" />
    </Box>
  );
}

function RowMessages({
  title,
  column,
  items,
  severity,
}: {
  title: string;
  column: string;
  items: Schemas['RowMessage'][];
  severity: 'error' | 'warning';
}) {
  if (!items.length) return null;
  return (
    <Box sx={{ mt: 2 }}>
      <Typography component="h3" variant="subtitle1" color={severity === 'error' ? 'error' : 'warning.main'}>
        {title}: {items.length}
      </Typography>
      <Table size="small" aria-label={title} sx={{ maxWidth: 720 }}>
        <TableHead>
          <TableRow>
            <TableCell scope="col">{t.line}</TableCell>
            <TableCell scope="col">{column}</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {items.map((m, i) => (
            <TableRow key={`${m.line}-${i}`}>
              <TableCell sx={{ fontVariantNumeric: 'tabular-nums' }}>{m.line}</TableCell>
              <TableCell>{m.message}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

export function OrgImportPage() {
  const role = useRole();
  const translate = useTranslate();
  const dp = useDataProvider<BmDataProvider>();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState<Step>({ kind: 'idle' });
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  if (role === undefined) return null;
  if (!can(role, 'importOrg')) {
    return (
      <Card sx={{ mt: 2 }}>
        <Title title={t.title} />
        <Forbidden />
      </Card>
    );
  }

  async function check(file: File) {
    setError(null);
    if (file.size > ORG_FILE_MAX_BYTES) {
      setStep({ kind: 'idle' });
      setError(ru.bm.problem.file_too_large);
      return;
    }
    setStep({ kind: 'checking', file });
    try {
      setStep({ kind: 'checked', file, summary: await dp.importOrg(file, true) });
    } catch (e) {
      setStep({ kind: 'idle' });
      setError(checkErrorText(e));
    }
  }

  async function apply() {
    if (step.kind !== 'checked') return;
    setConfirming(false);
    setError(null);
    setStep({ ...step, kind: 'applying' });
    try {
      setStep({ kind: 'applied', summary: await dp.importOrg(step.file, false) });
    } catch (e) {
      setStep({ ...step, kind: 'checked' });
      setError(statusOf(e) === 0 ? t.apply_network : checkErrorText(e));
    }
  }

  const reset = () => {
    setStep({ kind: 'idle' });
    setError(null);
    if (inputRef.current) inputRef.current.value = '';
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void check(file);
  };

  const summary = step.kind === 'checked' || step.kind === 'applying' ? step.summary : null;

  return (
    <Card sx={{ mt: 2 }}>
      <Title title={t.title} />
      <CardContent>
        {step.kind === 'applied' ? (
          <>
            <SummaryView summary={step.summary} applied />
            <Box sx={{ mt: 2, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
              <Button component={Link} to="/departments" variant="contained">
                {t.to_departments}
              </Button>
              <Button component={Link} to="/departments" variant="outlined">
                {t.set_limits}
              </Button>
              <Button onClick={reset}>{t.another}</Button>
            </Box>
          </>
        ) : (
          <>
            <Typography component="h2" variant="subtitle1" sx={{ fontWeight: 600 }}>
              {t.step1}
            </Typography>
            <Box
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              sx={{
                mt: 1,
                p: 3,
                border: '2px dashed',
                borderColor: dragOver ? 'primary.main' : 'divider',
                borderRadius: 1,
                textAlign: 'center',
              }}
            >
              <Typography component="span">{t.drop} </Typography>
              <Button component="label" htmlFor={inputId} variant="outlined" size="small">
                {t.choose.toLowerCase()}
              </Button>
              <input
                ref={inputRef}
                id={inputId}
                type="file"
                accept=".csv,text/csv"
                aria-label={t.choose}
                style={{ position: 'absolute', width: 1, height: 1, opacity: 0, overflow: 'hidden' }}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) void check(file);
                }}
              />
            </Box>
            <Box component="details" sx={{ mt: 1 }}>
              <summary>{t.format}</summary>
              <Box component="pre" lang="en" translate="no" sx={{ fontFamily: 'var(--bm-font-mono, monospace)', my: 1 }}>
                {'login;display_name;group;is_primary\nivanova;Иванова А. С.;AI-Marketing;1\nivanova;Иванова А. С.;AI-Analytics;0\npetrov;Петров И. И.;AI-Analytics;'}
              </Box>
            </Box>
            <Typography variant="body2" color="text.secondary">
              {t.format_text}
            </Typography>

            {step.kind === 'checking' && (
              <Typography role="status" sx={{ mt: 2 }}>
                {t.checking}
              </Typography>
            )}
            {error && (
              <Alert severity="error" role="alert" sx={{ mt: 2 }}>
                {error}
              </Alert>
            )}
            {summary && (
              <>
                <SummaryView summary={summary} applied={false} />
                <Typography component="h2" variant="subtitle1" sx={{ fontWeight: 600, mt: 3 }}>
                  {t.step3}
                </Typography>
                {summary.rows_applied === 0 && (
                  <Alert severity="warning" sx={{ my: 1 }}>
                    {t.nothing}
                  </Alert>
                )}
                {step.kind === 'applying' && <Typography role="status">{t.applying}</Typography>}
                <Box sx={{ mt: 1, display: 'flex', gap: 1, flexWrap: 'wrap' }}>
                  <Button
                    variant="contained"
                    disabled={summary.rows_applied === 0 || step.kind === 'applying'}
                    onClick={() => (summary.employees_removed > 0 ? setConfirming(true) : void apply())}
                  >
                    {t.apply}
                  </Button>
                  <Button onClick={reset}>{t.another}</Button>
                </Box>
                <ConfirmDialog
                  open={confirming}
                  danger
                  title={t.confirm_title}
                  text={translate('bm.org_import.confirm_text', { smart_count: summary.employees_removed })}
                  confirm={t.confirm_apply}
                  onClose={() => setConfirming(false)}
                  onConfirm={() => void apply()}
                />
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
