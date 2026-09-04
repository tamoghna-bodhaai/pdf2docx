import { Icon } from "./icons";
import Link from "next/link";

export function Brand() {
  return (
    <Link className="brand-lockup" href="/" prefetch={false} aria-label="PDF2DOCX tools">
      <span className="brand-mark" aria-hidden="true"><Icon name="file" /></span>
      <span><span className="wordmark">PDF<span>2</span>DOCX</span><small>Document workspace</small></span>
    </Link>
  );
}
