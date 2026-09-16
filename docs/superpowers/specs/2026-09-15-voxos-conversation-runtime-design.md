# VoxOS Conversation Runtime Design

## Goal

Turn UI input into concise assistant messages with durable session context, deterministic handling for known desktop/browser commands, and recoverable browser/service failures.

## Decisions

- The UI renders an `AssistantReply` (`text`, `kind`, optional `action`) rather than task dictionaries or Python errors.
- A `ConversationService` becomes the single UI entry point. It owns session context loading, task execution, reply shaping, and durable turn recording.
- PostgreSQL is the durable conversation source. Each turn is stored against a session; prompt context uses the latest 12 turns plus a bounded summary. Redis remains event/cache infrastructure only.
- Rule-first paths answer greetings locally and map browser aliases deterministically: `browser` opens the system default browser, `youtube` opens YouTube, explicit `chrome` remains desktop app launch. Unknown tasks use the existing LLM pipeline.
- Browser sessions must test both `Page.is_closed()` and `Browser.is_connected()` and recreate all Playwright state after a closed browser. Assistant search defaults to Bing to avoid Google CAPTCHA; explicit Google requests may return a friendly CAPTCHA result.
- Provider fallbacks apply only to transient connection/rate-limit failures. Invalid model/configuration errors become a user-safe configuration reply and remain detailed in logs/audit data.
- Wake-word initialization remains optional. Its unavailable-dependency state is presented as a non-blocking status, while text chat stays usable.

## User-visible behavior

- `hi` → a greeting, not a no-tool-step JSON object.
- `open browser` → opens the default browser and replies with a short confirmation.
- `open youtube` → opens YouTube without Strategist.
- `open chrome and search google` → reports the opened/search target in prose; CAPTCHA becomes an explanatory message.
- `now search whatsapp` → retains session context and recreates a closed automated browser if needed.
- Follow-up chat includes recent relevant turns from the same session.

## Boundaries

- This does not add cross-user identity, semantic/vector search, or a UI redesign.
- Existing permissions, audit records, task lifecycle, and tool risk checks remain authoritative.
- Database migrations must be additive and work with the existing async SQLAlchemy/Alembic stack.
