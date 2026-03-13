-- Add tool_calls_json and space_id columns to chat_messages
ALTER TABLE chat_messages ADD COLUMN tool_calls_json TEXT;
ALTER TABLE chat_messages ADD COLUMN space_id TEXT;
