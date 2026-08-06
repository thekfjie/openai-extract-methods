import { useEffect } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { canonicalSub, groupForPath } from '../components/layout/navigation';

export default function useNavigationSub(path) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const group = groupForPath(path);
  const requestedSub = searchParams.get('sub');
  const redirectTarget = group?.redirects?.[requestedSub] || '';
  const activeSub = canonicalSub(group, requestedSub);
  const activeItem = group?.items.find((item) => item.sub === activeSub) || null;

  useEffect(() => {
    if (redirectTarget) {
      navigate(redirectTarget, { replace: true });
      return;
    }
    if (!requestedSub || requestedSub === activeSub) return;
    const nextParams = new URLSearchParams(searchParams);
    nextParams.set('sub', activeSub);
    setSearchParams(nextParams, { replace: true });
  }, [activeSub, navigate, redirectTarget, requestedSub, searchParams, setSearchParams]);

  return {
    activeSub,
    activeItem,
    redirecting: Boolean(redirectTarget),
  };
}
