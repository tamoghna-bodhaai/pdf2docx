declare module "katex/contrib/auto-render" {
  interface Delimiter { left: string; right: string; display: boolean }
  export default function renderMathInElement(element: HTMLElement, options?: { delimiters?: Delimiter[]; throwOnError?: boolean }): void;
}
