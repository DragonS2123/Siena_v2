export {};

declare global {
  interface Window {
    sienaDesktop?: {
      minimize: () => Promise<void>;
      toggleMaximize: () => Promise<boolean>;
      close: () => Promise<void>;
      isMaximized: () => Promise<boolean>;
      saveCodeFile: (request: { content: string; suggestedName: string; language: string }) => Promise<{ saved: boolean; canceled: boolean; filename?: string }>;
      onMaximizedChange: (listener: (maximized: boolean) => void) => () => void;
    };
  }
}