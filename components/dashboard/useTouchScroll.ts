"use client";

import {
  useCallback,
  useEffect,
  useRef,
  type MouseEvent,
  type PointerEvent,
} from "react";
import type { DashboardView } from "@/components/dashboard/config";

const DRAG_THRESHOLD_PX = 7;
const CLICK_SUPPRESSION_MS = 400;

export function useTouchScroll(view: DashboardView) {
  const scrollRef = useRef<HTMLElement>(null);
  const dragRef = useRef({
    pointerId: -1,
    startY: 0,
    startScrollTop: 0,
    moved: false,
  });
  const suppressClickUntilRef = useRef(0);

  const onPointerDown = useCallback((event: PointerEvent<HTMLElement>) => {
    if (!event.isPrimary || (event.pointerType === "mouse" && event.button !== 0)) {
      return;
    }
    dragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startScrollTop: event.currentTarget.scrollTop,
      moved: false,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (drag.pointerId !== event.pointerId) return;

    const delta = drag.startY - event.clientY;
    if (!drag.moved && Math.abs(delta) < DRAG_THRESHOLD_PX) return;
    drag.moved = true;
    event.currentTarget.scrollTop = drag.startScrollTop + delta;
    event.preventDefault();
  }, []);

  const onPointerEnd = useCallback((event: PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (drag.pointerId !== event.pointerId) return;
    if (drag.moved) {
      suppressClickUntilRef.current = performance.now() + CLICK_SUPPRESSION_MS;
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current.pointerId = -1;
  }, []);

  const onClickCapture = useCallback((event: MouseEvent<HTMLElement>) => {
    if (performance.now() >= suppressClickUntilRef.current) return;
    event.preventDefault();
    event.stopPropagation();
  }, []);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [view]);

  return {
    scrollRef,
    scrollHandlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: onPointerEnd,
      onPointerCancel: onPointerEnd,
      onClickCapture,
    },
  };
}
