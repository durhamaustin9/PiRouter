"use client";

import {
  useCallback,
  useEffect,
  useRef,
  type MouseEvent,
  type PointerEvent,
} from "react";
import type { DashboardView } from "@/components/dashboard/config";

const DRAG_THRESHOLD_PX = 12;
const CLICK_SUPPRESSION_MS = 400;
const INTERACTIVE_SELECTOR =
  "button, a, input, select, textarea, [role='button'], [data-touch-control]";

function isInteractiveTarget(target: EventTarget | null) {
  return (
    target instanceof Element && target.closest(INTERACTIVE_SELECTOR) !== null
  );
}

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
    if (isInteractiveTarget(event.target)) {
      dragRef.current.pointerId = -1;
      return;
    }
    dragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startScrollTop: event.currentTarget.scrollTop,
      moved: false,
    };
  }, []);

  const onPointerMove = useCallback((event: PointerEvent<HTMLElement>) => {
    const drag = dragRef.current;
    if (drag.pointerId !== event.pointerId) return;

    const delta = drag.startY - event.clientY;
    if (!drag.moved && Math.abs(delta) < DRAG_THRESHOLD_PX) return;
    if (!drag.moved) event.currentTarget.setPointerCapture(event.pointerId);
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
    if (isInteractiveTarget(event.target)) return;
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
