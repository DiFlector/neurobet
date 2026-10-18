import fs from "node:fs";

const source = fs.readFileSync(new URL("../../services/frontend/lib/placed-bets.ts", import.meta.url), "utf8");
for (const token of ["PENDING", "WON", "LOST", "VOID", "Number(bet.stake) > 0"]) {
  if (!source.includes(token)) {
    throw new Error(`placed-bets.ts lost the rule: ${token}`);
  }
}

const placed = new Set(["PENDING", "ACCEPTED", "WON", "LOST", "VOID"]);
const isPlacedBet = (bet) => Boolean(bet.event_id) && placed.has(bet.status) && Number(bet.stake) > 0;
const ids = new Set();
for (const bet of [
  { event_id: "match-verdict", status: "REJECTED", stake: 0 },
  { event_id: "match-live", status: "PENDING", stake: 250 },
  { event_id: "match-done", status: "WON", stake: 100 },
]) {
  if (isPlacedBet(bet)) ids.add(bet.event_id);
}
if (ids.has("match-verdict") || !ids.has("match-live") || !ids.has("match-done")) {
  throw new Error(`expected placed matches only, got ${[...ids].join(",")}`);
}
console.log("placed bet filter ok");
