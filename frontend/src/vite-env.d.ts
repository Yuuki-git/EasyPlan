/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_PLANNER_PROVIDER?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
