import polyglotI18nProvider from 'ra-i18n-polyglot';
import { ru } from './ru';

/** Единственный язык интерфейса — русский (принцип IX). Locale 'ru' включает русские формы множественного числа. */
export const i18nProvider = polyglotI18nProvider(() => ru, 'ru', [{ locale: 'ru', name: 'Русский' }], {
  allowMissing: true,
});
