import { useEffect, useState } from "react";

// 哈希路由：#/live、#/stats、#/review/<对局文件名>
export type Route = { page: "live" | "stats" | "review"; arg?: string };

function parse(): Route {
  const [, page, ...rest] = location.hash.replace(/^#/, "").split("/");
  if (page === "stats") return { page: "stats" };
  if (page === "review") return { page: "review", arg: rest.length ? decodeURIComponent(rest.join("/")) : undefined };
  return { page: "live" };
}

export function useRoute(): Route {
  const [r, setR] = useState(parse);
  useEffect(() => {
    const on = () => setR(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return r;
}

export const go = (hash: string) => {
  location.hash = hash;
};

export const reviewHref = (file: string) => `#/review/${encodeURIComponent(file)}`;
