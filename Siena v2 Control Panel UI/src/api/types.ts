export type Model = {
  name: string;
  tag: string;
  family: string | null;
  parameter_size: string | null;
  quantization: string | null;
  size: number | null;
  loaded: boolean;
};

export type ModelRole = { role: string; model: string; missing: boolean };
export type ModelsPayload = {
  available: boolean;
  models: Model[];
  roles: ModelRole[];
  error: string | null;
  refreshed_at: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  content: string;
  model?: string | null;
  metadata?: Record<string, unknown>;
  attachments?: Array<{ id: string; original_name: string; kind: string; ocr_status?: string; vision_status?: string }>;
};

export type ChatAttachment = {
  name: string;
  type: "text" | "image";
  mime: string;
  content?: string;
  data_url?: string;
};

export type Conversation = {
  id: string;
  title: string;
  model_override?: string | null;
  messages?: Message[];
};
