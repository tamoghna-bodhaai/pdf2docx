import type { SVGProps } from "react";

type IconName = "file" | "upload" | "images" | "split" | "history" | "menu" | "back" | "download" | "trash";
const paths: Record<IconName, string[]> = {
  file: ["M7 3.75h7l4 4V20.25H7z", "M14 3.75v4h4", "M9.5 12h6", "M9.5 15h4.25"],
  upload: ["M12 16V5", "m8 9 4-4 4 4", "M5 14v4.25C5 19.22 5.78 20 6.75 20h10.5c.97 0 1.75-.78 1.75-1.75V14"],
  images: ["M4.5 6.5h15v11h-15z", "m6.5 15 3.5-4 2.5 2.5 2-2.5 3 3.5", "M15.5 9h.01"],
  split: ["M7 3.75h10v16.5H7z", "M12 4v16", "m9.5 10-2 2 2 2", "m14.5 10 2 2-2 2"],
  history: ["M4.75 12a7.25 7.25 0 1 0 2.12-5.13", "M4.75 5.5v4.25H9", "M12 8v4.25l2.75 1.75"],
  menu: ["M5 7h14", "M5 12h14", "M5 17h14"],
  back: ["m14.5 6-6 6 6 6"],
  download: ["M12 4v11", "m8 11 4 4 4-4", "M5 19h14"],
  trash: ["M7 7h10", "m9 7 .5 12h5L15 7", "M9.5 4h5"],
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return <svg viewBox="0 0 24 24" aria-hidden="true" {...props}>{paths[name].map((path) => <path d={path} key={path} />)}</svg>;
}
