export interface AuthRecoveryState {
  token: null;
  showAuthModal: true;
  pendingIntent: string;
  appState: 'INITIAL';
  error: null;
}

export function isUnauthorizedResponse(response: Pick<Response, 'status'>): boolean {
  return response.status === 401;
}

export function buildAuthRecoveryState(pendingIntent: string): AuthRecoveryState {
  return {
    token: null,
    showAuthModal: true,
    pendingIntent,
    appState: 'INITIAL',
    error: null,
  };
}
