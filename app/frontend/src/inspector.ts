import { createContext, useContext } from "react";

import type { SourceTarget } from "./types";

export interface InspectorApi {
  openSource: (t: SourceTarget) => void;
  close: () => void;
}

export const InspectorContext = createContext<InspectorApi>({ openSource: () => {}, close: () => {} });

export function useInspector() {
  return useContext(InspectorContext);
}
