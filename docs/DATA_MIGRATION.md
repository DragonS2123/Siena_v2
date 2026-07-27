# Data Migration

Before the cleanup an external full checkpoint was created. On first startup,
obsolete settings are removed only after creating
`settings.pre-core-cleanup.bak`. Conversation schema migration creates
`conversations.pre-core-cleanup.bak`, runs transactionally and is idempotent.
