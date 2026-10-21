// Общая настройка тестов (T095). Среда общая и медленная (2 CPU, параллельно идут прогоны backend):
// за 1 с ожидания findBy*/waitFor и 5 с на тест экран с react-admin иногда не успевает отрисоваться.
// Поднимаются только пределы ожидания; сами проверки не меняются.
import { configure } from '@testing-library/dom';

configure({ asyncUtilTimeout: 5000 });
