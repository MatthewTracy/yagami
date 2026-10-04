import type { CSSProperties } from "react";

type Name =
  | "shield"
  | "menu"
  | "memory"
  | "activity"
  | "settings"
  | "plus"
  | "message"
  | "arrow"
  | "paperclip"
  | "close"
  | "document";

const paths: Record<Name, string> = {
  shield: "M12 3 4 6v6c0 4.5 8 9 8 9s8-4.5 8-9V6l-8-3Z M8.5 12l2.5 2.5 4.5-5",
  menu: "M4 6h16M4 12h16M4 18h16",
  memory:
    "M9 3a3 3 0 0 0-3 3v1a4 4 0 0 0-2 7 4 4 0 0 0 5 6h3V4a3 3 0 0 0-3-1ZM15 3a3 3 0 0 1 3 3v1a4 4 0 0 1 2 7 4 4 0 0 1-5 6h-3M7 10h2M15 10h2M7 16h2M15 16h2",
  activity: "M3 12h4l3-8 4 16 3-8h4",
  settings:
    "M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3Z",
  plus: "M12 5v14M5 12h14",
  message: "M4 4h16v13H9l-5 4V4Z M8 8h8M8 12h5",
  arrow: "M5 12h14M13 6l6 6-6 6",
  paperclip: "m9 17 8-8a3 3 0 0 0-4-4L5 13a5 5 0 0 0 7 7l8-8M8 14l7-7",
  close: "m6 6 12 12M6 18 18 6",
  document: "M5 3h9l5 5v13H5V3ZM14 3v5h5M9 12h6M9 16h6",
};

export function Icon({
  name,
  size = 18,
  style,
}: {
  name: Name;
  size?: number;
  style?: CSSProperties;
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      style={style}
    >
      <path d={paths[name]} />
    </svg>
  );
}
