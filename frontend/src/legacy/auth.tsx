import {useEffect,type ReactNode} from 'react';
import {useSessionStore,initializeSession} from '../state/session';
export type {AuthSession,Tenant} from '../api/session.types';
export {clearLocalWorkspace} from '../state/session';
export function useAuth(){return useSessionStore()}
export function AuthProvider({children}:{children:ReactNode}){useEffect(()=>initializeSession(),[]);return <>{children}</>}
