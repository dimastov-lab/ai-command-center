import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

export const en = {
  home: 'Home',
  workspace: 'Workspace',
  agents: 'Agents',
  execution: 'Execution',
  git: 'Git Center',
  tasks: 'Tasks',
  reports: 'Reports',
  artifacts: 'Artifacts',
  reviewCenter: 'Review Center',
  settings: 'Settings',
  greeting: 'Good morning, Dmitry',
  greetingSub: "Here's what's happening with your projects today.",
  newTask: 'New Task',
  projects: 'Projects',
  reviews: 'Reviews',
  all_healthy: 'All healthy',
  running: 'running',
  in_progress: 'in progress',
  pending: 'pending',
  executionQueue: 'Execution queue',
  projectHealth: 'Project health',
  recentActivity: 'Recent activity',
  quickOverview: 'Quick overview',
  quickActions: 'Quick actions',
  viewAll: 'View all',
  operational: 'Operational',
  online: 'Online',
  background: 'Background',
}

export const ru = {
  home: 'Главная',
  workspace: 'Воркспейс',
  agents: 'Агенты',
  execution: 'Выполнение',
  git: 'Git-центр',
  tasks: 'Задачи',
  reports: 'Отчёты',
  artifacts: 'Артефакты',
  reviewCenter: 'Ревью',
  settings: 'Настройки',
  greeting: 'Доброе утро, Dmitry',
  greetingSub: 'Вот что происходит с вашими проектами сегодня.',
  newTask: 'Новая задача',
  projects: 'Проекты',
  reviews: 'Ревью',
  all_healthy: 'Все в норме',
  running: 'активны',
  in_progress: 'в работе',
  pending: 'ожидает',
  executionQueue: 'Очередь выполнения',
  projectHealth: 'Здоровье проектов',
  recentActivity: 'Последняя активность',
  quickOverview: 'Обзор',
  quickActions: 'Быстрые действия',
  viewAll: 'Все',
  operational: 'Работает',
  online: 'В сети',
  background: 'Фон',
}

i18n
  .use(initReactI18next)
  .init({
    resources: {
      en: { translation: en },
      ru: { translation: ru },
    },
    lng: 'en',
    fallbackLng: 'en',
    interpolation: { escapeValue: false },
  })

export function toggleLang() {
  i18n.changeLanguage(i18n.language === 'en' ? 'ru' : 'en')
}

export default i18n
