import type { CSSProperties } from "react";
import type { CardT } from "../store";

// 没有卡图：按卡牌 id 稳定地挑一组渐变色
const PALETTE: [string, string][] = [
  ["#7d3bd6", "#2a0f66"], ["#1fae6b", "#063d2a"], ["#a01e6a", "#33062a"], ["#d9a21b", "#5c3a06"],
  ["#e04a2f", "#4d0d07"], ["#3a5bd6", "#0f1a66"], ["#e02a3a", "#3d0a1a"], ["#3fc0ff", "#0b3a7a"],
  ["#ff6b2b", "#4a0b1a"], ["#8f7bff", "#2b1f7a"], ["#ff9a3d", "#5a1e06"], ["#2fc5b5", "#06403c"],
];

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h;
}

function initials(name: string): string {
  const caps = name.replace(/[^A-Z]/g, "");
  return (caps || name).slice(0, 2).toUpperCase();
}

type Props = { card?: CardT; hidden?: boolean; fresh?: boolean; plan?: string; style?: CSSProperties; className?: string };

export function Card({ card, hidden, fresh, plan, style, className = "" }: Props) {
  if (hidden || !card)
    return (
      <div className={`card hidden ${className}`} style={style} title="对手未揭示的牌">
        <div className="ini">?</div>
        <div className="nm">未揭示</div>
      </div>
    );
  const [a, b] = PALETTE[hash(card.id) % PALETTE.length];
  const cls = ["card", fresh ? "fresh" : "", plan ? "plan" : "", className].join(" ");
  const tip = `${card.name}${card.cost != null ? ` · ${card.cost} 费` : ""}${card.power != null ? ` ${card.power} 战力` : ""}${card.ability ? `\n${card.ability}` : ""}`;
  return (
    <div className={cls} style={{ ...style, ["--a" as any]: a, ["--b" as any]: b }} title={tip}>
      <div className="ini">{initials(card.name || card.id)}</div>
      {card.cost != null && <span className="co">{card.cost}</span>}
      {card.power != null && <span className="pw">{card.power}</span>}
      <div className="nm">{card.name || card.id}</div>
    </div>
  );
}
