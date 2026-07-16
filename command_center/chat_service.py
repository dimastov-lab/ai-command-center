"""Project Chat: conversation storage + a small provider abstraction.

Three providers, in increasing order of external dependency:

- `LocalProvider` — always available. No model call is made; the user writes both
  sides of the conversation (or pastes in a response copied from elsewhere). This is
  the only provider the app can depend on being present.
- `ClaudeCodeChatProvider` — shells out to the local `claude` CLI (same binary as
  `agent_runner`), read-only (`task_type="review"`, so the file-edit tools are
  disallowed — a chat turn must never modify the repository).
- `OpenAIChatProvider` — only reports itself available when `OPENAI_API_KEY` *and*
  `OPENAI_MODEL` are both set in the environment and the `openai` package is
  importable. The API key is read directly from the environment by the OpenAI SDK
  itself; this module never reads, stores, logs, or echoes it. The model name is never
  hard-coded — it always comes from `OPENAI_MODEL`.

If `OPENAI_API_KEY` is absent, `OpenAIChatProvider.is_available()` returns a clear
message and the rest of the app (including the other two providers) keeps working —
the `openai` import itself is deferred into `send()` so its mere absence from the
environment can never break app startup.
"""

from __future__ import annotations

import os
from pathlib import Path

from command_center import agent_runner, models, storage

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = storage.resolve_data_dir(ROOT)
CHATS_FILE = DATA_DIR / "chats.json"
CHATS_EXAMPLE_FILE = ROOT / "data" / "chats.example.json"


# --------------------------------------------------------------------------
# Conversation storage
# --------------------------------------------------------------------------


def load_conversations() -> list[dict]:
    storage.ensure_seeded(CHATS_FILE, [])
    data = storage.read_json(CHATS_FILE, [])
    return data if isinstance(data, list) else []


def save_conversations(conversations: list[dict]) -> None:
    storage.atomic_write_json(CHATS_FILE, conversations)


def get_conversation(conversations: list[dict], conversation_id: str) -> dict | None:
    for conversation in conversations:
        if conversation.get("id") == conversation_id:
            return conversation
    return None


def append_message(conversations: list[dict], conversation_id: str, message: dict) -> None:
    conversation = get_conversation(conversations, conversation_id)
    if conversation is None:
        raise ValueError(f"Unknown conversation: {conversation_id}")
    conversation.setdefault("messages", []).append(message)
    conversation["updated_at"] = models.iso_now()
    save_conversations(conversations)


# --------------------------------------------------------------------------
# Provider abstraction
# --------------------------------------------------------------------------


class ChatProvider:
    name: str = "base"
    label: str = "Base"

    def is_available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def send(
        self,
        *,
        messages: list[dict],
        project_context: str,
        project_id: str | None,
        repository_path: str | None,
        timeout_seconds: int,
    ) -> str:
        raise NotImplementedError


class LocalProvider(ChatProvider):
    name = "local"
    label = "Локальный / ручной режим"

    def is_available(self) -> tuple[bool, str]:
        return True, "Ручной режим: вы вводите ответ самостоятельно, без вызова модели."

    def send(self, **_kwargs) -> str:
        raise NotImplementedError(
            "Локальный режим не отправляет сообщения автоматически — введите ответ вручную."
        )


class ClaudeCodeChatProvider(ChatProvider):
    name = "claude_code"
    label = "Claude Code (локальный CLI)"

    def is_available(self) -> tuple[bool, str]:
        if agent_runner.claude_cli_available():
            return True, ""
        return False, "CLI `claude` не найден в PATH — установите Claude Code для этого режима."

    def send(
        self,
        *,
        messages: list[dict],
        project_context: str,
        project_id: str | None,
        repository_path: str | None,
        timeout_seconds: int,
    ) -> str:
        available, reason = self.is_available()
        if not available:
            raise RuntimeError(reason)

        if repository_path and project_id:
            cwd = agent_runner.validate_repository(project_id, repository_path)
        else:
            cwd = agent_runner.ROOT

        prompt = _build_prompt(messages, project_context)
        # task_type="review" forces the read-only tool-permission set — a chat turn
        # must never modify the repository.
        result = agent_runner.run_claude_code(
            repository_path=cwd,
            prompt=prompt,
            task_type="review",
            timeout_seconds=timeout_seconds,
        )
        if result.status != "completed":
            raise RuntimeError(result.stderr.strip() or f"Claude Code завершился со статусом «{result.status}».")
        return agent_runner.extract_result_text(result.stdout).strip()


class OpenAIChatProvider(ChatProvider):
    name = "openai"
    label = "OpenAI (Responses API)"

    def is_available(self) -> tuple[bool, str]:
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            return False, (
                "Переменная OPENAI_API_KEY не задана — режим OpenAI недоступен. "
                "Использование API оплачивается отдельно от подписки ChatGPT."
            )
        model = os.environ.get("OPENAI_MODEL", "").strip()
        if not model:
            return False, "OPENAI_API_KEY задан, но OPENAI_MODEL — нет. Укажите модель явно."
        try:
            import openai  # noqa: F401
        except ImportError:
            return False, "Пакет `openai` не установлен в окружении."
        return True, ""

    def send(
        self,
        *,
        messages: list[dict],
        project_context: str,
        project_id: str | None,
        repository_path: str | None,
        timeout_seconds: int,
    ) -> str:
        available, reason = self.is_available()
        if not available:
            raise RuntimeError(reason)

        from openai import OpenAI  # deferred: absence must never break app startup

        client = OpenAI()  # reads OPENAI_API_KEY from the environment itself
        model = os.environ.get("OPENAI_MODEL", "").strip()

        input_items = []
        if project_context:
            input_items.append({"role": "system", "content": f"Контекст проекта:\n{project_context}"})
        for message in messages:
            role = "user" if message.get("role") == "user" else "assistant"
            input_items.append({"role": role, "content": message.get("content", "")})

        response = client.responses.create(model=model, input=input_items, timeout=timeout_seconds)
        return getattr(response, "output_text", "").strip()


def _build_prompt(messages: list[dict], project_context: str) -> str:
    lines: list[str] = []
    if project_context:
        lines.append("Контекст проекта:\n" + project_context)
        lines.append("")
    lines.append(
        "Ты участвуешь в текстовом чате внутри AI Command Center. Отвечай кратко и по "
        "делу на последнее сообщение пользователя, учитывая историю переписки ниже. "
        "Это режим только для чтения — не изменяй файлы."
    )
    lines.append("")
    for message in messages:
        role_label = "Пользователь" if message.get("role") == "user" else "Ассистент"
        lines.append(f"{role_label}: {message.get('content', '')}")
    lines.append("Ассистент:")
    return "\n".join(lines)


def available_providers() -> list[ChatProvider]:
    return [LocalProvider(), ClaudeCodeChatProvider(), OpenAIChatProvider()]


def get_provider(name: str) -> ChatProvider:
    for provider in available_providers():
        if provider.name == name:
            return provider
    raise ValueError(f"Unknown provider: {name}")
