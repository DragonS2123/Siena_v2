export {};

declare global {
  interface Window {
    sienaDesktop?: {
      minimize: () => Promise<void>;
      toggleMaximize: () => Promise<boolean>;
      close: () => Promise<void>;
      isMaximized: () => Promise<boolean>;
      onMaximizedChange: (listener: (maximized: boolean) => void) => () => void;
    };
  }
}