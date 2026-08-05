# AML Service — Архитектурное описание

Версия: 1.0  
Дата: 2026-08-05  
Статус: финальная (для банковской приёмки)

---

## 1. Обзор

AML Service — программный модуль автоматизированного мониторинга операций на предмет соответствия требованиям Федерального закона № 115-ФЗ «О противодействии легализации (отмыванию) доходов, полученных преступным путём, и финансированию терроризма».

Система реализует полный цикл работы с подозрительными операциями: от автоматического обнаружения и оценки рисков клиентов до формирования сообщений в Росфинмониторинг (SAR/STR/CTR) и ведения аудиторского следа.

---

## 2. Архитектурный стиль

Система построена по **слоистой (layered) архитектуре**:

```
Презентационный слой (Streamlit UI)
         │
Сервисный слой (Store-модули: alert_store, case_store, sar_store, …)
         │
Слой данных (SQLite — отдельная база на каждый модуль)
```

**Ключевые принципы:**
- Нет бизнес-логики в UI-слое.
- Нет прямого SQL вне Store-модулей.
- Каждый Store-модуль управляет своей SQLite-базой.
- Нет циклических импортов между модулями.

---

## 3. Модульный состав

| Модуль | Файл | База данных | Назначение |
|--------|------|-------------|------------|
| Alert Store | `command_center/alert_store.py` | `aml_alerts.db` | Алерты, состояния, дедупликация |
| Customer Store | `command_center/customer_store.py` | `aml_customers.db` | Клиентский профиль, KYC, риск-тир |
| Risk Store | `command_center/risk_store.py` | `aml_risk.db` | Риск-скоринг, мост Risk→Alert |
| Rule Engine | `command_center/rule_engine.py` | `aml_rules.db` | Движок правил (115-ФЗ + FATF) |
| Evidence Store | `command_center/evidence_store.py` | `aml_evidence.db` | Неизменяемые доказательства |
| Case Store | `command_center/case_store.py` | `aml_cases.db` | Дела (расследование) |
| SAR Store | `command_center/sar_store.py` | `aml_sars.db` | Сообщения в регулятор |
| Compliance Store | `command_center/compliance_store.py` | (читает все БД) | Агрегация метрик, health-score |
| Seed 115-ФЗ | `command_center/seed_rules_115fz.py` | `aml_rules.db` | Идемпотентная загрузка правил |

---

## 4. Жизненный цикл алерта

```
GENERATED
    │
    ├─ assign_owner() → IN_TRIAGE
    │                       │
    │           submit_for_review() → PENDING_REVIEW
    │                                       │
    │                   ┌───────────────────┤
    │           approve_alert()        escalate_alert()
    │                   │                   │
    │              REVIEWED           ESCALATED
    │                   │
    │            close_alert()
    │                   │
    │              CLOSED (outcome: true_positive | false_positive | …)
    │
    └─ archive_alert() → ARCHIVED
```

Переходы защищены оптимистичной конкуренцией (`expected_state`) — конфликт обновлений вызывает `LostUpdate`.

---

## 5. Жизненный цикл дела (Case)

```
OPEN
 │
 ├─ start_investigation() → UNDER_INVESTIGATION
 │                               │
 │                 submit_for_review() → PENDING_REVIEW
 │                                           │
 │                          ┌────────────────┤
 │                   close_case()     escalate_to_sar()
 │                          │                │
 │                       CLOSED      ESCALATED_TO_SAR
```

Закрытие и эскалация в SAR — только для ролей `ComplianceOfficer` / `MLRO` (`PermissionDenied` иначе).

---

## 6. Жизненный цикл SAR

```
DRAFT → UNDER_REVIEW → APPROVED → SUBMITTED → ACKNOWLEDGED
  ↑                                   ↑
  └────── reject_sar() ←──────────────┘ (REJECTED → reopen_to_draft())
```

Сроки подачи (115-ФЗ):
- STR (подозрительная операция) — 3 рабочих дня (ст. 7 115-ФЗ)
- CTR (обязательный контроль ≥ 600 000 руб.) — 1 рабочий день (ст. 6 115-ФЗ)

---

## 7. Движок правил

Правила оцениваются событийно. Каждое правило задаёт:
- `condition_field` — поле события (например, `amount`, `country`, `pep_flag`)
- `condition_operator` — оператор (`>=`, `in_list`, `eq`, `contains`, …)
- `condition_value` / `value_list` — пороговое значение или список

При срабатывании правило автоматически создаёт алерт с заданным `alert_type` и `priority_weight`.

Предустановленные правила 115-ФЗ (загружаются при старте):

| № | Название | Статья | Порог |
|---|----------|--------|-------|
| 1 | CTR ≥ 600 000 ₽ | Ст. 6 | ≥ 600 000 |
| 2 | CTR ≥ 5 000 000 ₽ | Ст. 6.1 | ≥ 5 000 000 |
| 3 | STR ≥ 100 000 ₽ | Ст. 7 | ≥ 100 000 |
| 4 | Структурирование | Ст. 7 | — |
| 5 | PEP-признак | Ст. 7 | flag = true |
| 6 | Негативные медиа | Ст. 7 | flag = true |
| 7 | FATF Blacklist | FATF | 25 стран |
| 8 | OFAC Sanctions | OFAC | 7 стран |
| 9 | Отрасль высокого риска | Ст. 7 | — |
| 10 | Продукт высокого риска | Ст. 7 | — |
| 11 | Тир риска high/pep | Ст. 7 | — |

---

## 8. Хранилище доказательств (Evidence Store)

Записи доказательств **иммутабельны** — нет update/delete. Поддерживаемые типы:
`document`, `kyc_document`, `adverse_media`, `transaction_record`, `correspondence`, `screenshot`, `third_party_report`, `other`.

Доказательства привязываются к любой сущности: `alert`, `case`, `customer`, `sar`.

---

## 9. Compliance Health Score

Составной показатель 0–100 используется в Compliance Dashboard:

| Пиллар | Вес | Формула |
|--------|-----|---------|
| KYC Completion | 25 | `25 × (kyc_verified / total_customers)` |
| SAR Filing | 25 | `max(0, 25 − 5 × overdue_sars)` |
| Alert Closure | 25 | `25 × min(closed_30d / recent_30d, 1)` |
| Case Resolution | 25 | `25 × min(closed_30d / (active + closed_30d), 1)` |

Рейтинги: `SATISFACTORY` (≥85), `NEEDS_IMPROVEMENT` (≥70), `DEFICIENT` (≥50), `CRITICAL` (<50).

---

## 10. Технологический стек

| Компонент | Технология | Версия |
|-----------|-----------|--------|
| Язык | Python | 3.13 |
| UI | Streamlit | ≥ 1.50 |
| БД | SQLite | 3.x (WAL mode) |
| Контейнер | Docker | 24+ |
| Оркестровка | Docker Compose | v2 |

---

## 11. Развёртывание (Docker)

```
docker compose -f docker-compose.aml.yml up -d
```

При первом запуске entrypoint автоматически:
1. Создаёт директорию `/data`
2. Загружает 11 правил 115-ФЗ (идемпотентно)
3. Запускает Streamlit на порту 8501

Данные хранятся в именованном томе `aml-data` (монтируется в `/data`).

---

## 12. Аудиторский след

Каждый Store-модуль ведёт собственный audit log (таблицы `alert_audit_log`, `case_audit_log`, `sar_audit_log`). Записи содержат: `actor`, `action`, `from_state`, `to_state`, `ts`, `extra` (JSON).

Аудиторский след **доступен только на чтение** из UI и **не допускает удаления** записей.
