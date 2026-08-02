import { useCallback, useEffect, useState } from 'react'

function branchFromUrl(): string {
  if (typeof window === 'undefined') return 'main'
  return new URLSearchParams(window.location.search).get('branch') || 'main'
}

export function useBranchContext() {
  const [branchId, setBranchState] = useState(branchFromUrl)

  useEffect(() => {
    const onNavigation = () => setBranchState(branchFromUrl())
    window.addEventListener('popstate', onNavigation)
    return () => window.removeEventListener('popstate', onNavigation)
  }, [])

  const setBranchId = useCallback((nextBranchId: string) => {
    const normalized = nextBranchId.trim() || 'main'
    const url = new URL(window.location.href)
    url.searchParams.set('branch', normalized)
    url.searchParams.delete('session')
    window.history.pushState({}, '', url)
    setBranchState(normalized)
    window.dispatchEvent(new PopStateEvent('popstate'))
  }, [])

  return { branchId, setBranchId }
}
