import { useRef, useState, useCallback, useEffect } from 'react'

const THRESHOLD = 60    // px to pull before triggering refresh
const MAX_PULL = 100    // max translateY
const RESISTANCE = 0.4  // dampening factor

export default function PullToRefresh({ onRefresh, children }) {
  const containerRef = useRef(null)
  const startY = useRef(0)
  const currentY = useRef(0)
  const pulling = useRef(false)
  const [pullDistance, setPullDistance] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [phase, setPhase] = useState('idle') // idle | pulling | refreshing | done

  const getScrollTop = useCallback(() => {
    const el = containerRef.current
    if (!el) return 1
    // Check if the scrollable parent (.main-content) is at top
    const main = el.closest('.main-content')
    return main ? main.scrollTop : window.scrollY
  }, [])

  const onTouchStart = useCallback((e) => {
    if (refreshing) return
    if (getScrollTop() > 0) return
    startY.current = e.touches[0].clientY
    currentY.current = startY.current
    pulling.current = true
  }, [refreshing, getScrollTop])

  const onTouchMove = useCallback((e) => {
    if (!pulling.current || refreshing) return
    if (getScrollTop() > 0) {
      pulling.current = false
      setPullDistance(0)
      setPhase('idle')
      return
    }

    currentY.current = e.touches[0].clientY
    const delta = (currentY.current - startY.current) * RESISTANCE

    if (delta <= 0) {
      setPullDistance(0)
      setPhase('idle')
      return
    }

    const dist = Math.min(delta, MAX_PULL)
    setPullDistance(dist)
    setPhase(dist >= THRESHOLD ? 'ready' : 'pulling')
  }, [refreshing, getScrollTop])

  const onTouchEnd = useCallback(async () => {
    if (!pulling.current) return
    pulling.current = false

    if (pullDistance >= THRESHOLD && !refreshing) {
      setRefreshing(true)
      setPhase('refreshing')
      setPullDistance(THRESHOLD * 0.7)

      try {
        await onRefresh?.()
      } catch {}

      setPhase('done')
      // Brief pause to show success
      await new Promise(r => setTimeout(r, 300))
      setRefreshing(false)
      setPullDistance(0)
      setPhase('idle')
    } else {
      setPullDistance(0)
      setPhase('idle')
    }
  }, [pullDistance, refreshing, onRefresh])

  // Prevent overscroll on iOS
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const prevent = (e) => {
      if (pulling.current && pullDistance > 0) {
        e.preventDefault()
      }
    }
    el.addEventListener('touchmove', prevent, { passive: false })
    return () => el.removeEventListener('touchmove', prevent)
  }, [pullDistance])

  const spinnerProgress = Math.min(pullDistance / THRESHOLD, 1)
  const spinnerRotation = spinnerProgress * 360

  return (
    <div
      ref={containerRef}
      className="ptr-container"
      onTouchStart={onTouchStart}
      onTouchMove={onTouchMove}
      onTouchEnd={onTouchEnd}
    >
      {/* Minimalist indicator */}
      <div
        className={`ptr-indicator ${phase}`}
        style={{
          transform: `translateY(${pullDistance - 40}px)`,
          opacity: phase === 'idle' ? 0 : Math.min(spinnerProgress, 1),
        }}
      >
        <div className="ptr-spinner-wrap">
          {phase === 'refreshing' ? (
            <div className="ptr-spinner spinning" />
          ) : phase === 'done' ? (
            <svg className="ptr-check" viewBox="0 0 24 24" width="18" height="18">
              <path d="M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z" fill="currentColor" />
            </svg>
          ) : (
            <div
              className="ptr-spinner"
              style={{
                transform: `rotate(${spinnerRotation}deg)`,
              }}
            />
          )}
        </div>
      </div>

      {/* Content */}
      <div
        className="ptr-content"
        style={{
          transform: pullDistance > 0 ? `translateY(${pullDistance * 0.5}px)` : 'none',
          transition: pulling.current ? 'none' : 'transform 0.3s cubic-bezier(0.2, 0, 0, 1)',
        }}
      >
        {children}
      </div>
    </div>
  )
}
