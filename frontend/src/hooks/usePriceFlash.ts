"use client";

import { useEffect, useRef, useState } from "react";

export type FlashClass = "" | "flash-up" | "flash-down";

/**
 * Returns "flash-up" / "flash-down" briefly after the price changes. Removing
 * the class lets the CSS transition fade the highlight out.
 */
export function usePriceFlash(price: number | null | undefined, holdMs = 300): FlashClass {
  const previous = useRef(price);
  const [flash, setFlash] = useState<FlashClass>("");

  useEffect(() => {
    const before = previous.current;
    previous.current = price;
    if (price == null || before == null || price === before) return;
    setFlash(price > before ? "flash-up" : "flash-down");
    const timer = setTimeout(() => setFlash(""), holdMs);
    return () => clearTimeout(timer);
  }, [price, holdMs]);

  return flash;
}
