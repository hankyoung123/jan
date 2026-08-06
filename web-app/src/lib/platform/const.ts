/**
 * Platform Feature Configuration
 * Centralized feature flags for different platforms
 */

import { PlatformFeature } from './types'
import { isPlatformTauri, isPlatformIOS, isPlatformAndroid } from './utils'

/**
 * Platform Features Configuration
 * Centralized feature flags for different platforms
 */
export const PlatformFeatures: Record<PlatformFeature, boolean> = {
  // System integrations (logs, file explorer, etc.)
  [PlatformFeature.SYSTEM_INTEGRATIONS]:
    isPlatformTauri() && !isPlatformIOS() && !isPlatformAndroid(),

  // HTTPS proxy
  [PlatformFeature.HTTPS_PROXY]:
    isPlatformTauri() && !isPlatformIOS() && !isPlatformAndroid(),

  // Default model providers (OpenAI, Anthropic, etc.) - disabled for web-only Jan builds
  [PlatformFeature.DEFAULT_PROVIDERS]: isPlatformTauri(),

  // Projects management
  [PlatformFeature.PROJECTS]:
    isPlatformTauri() && !isPlatformIOS() && !isPlatformAndroid(),

  // Analytics and telemetry - disabled for web
  [PlatformFeature.ANALYTICS]:
    isPlatformTauri() && !isPlatformIOS() && !isPlatformAndroid(),

  // Web-specific automatic model selection from jan provider - enabled for web only
  [PlatformFeature.WEB_AUTO_MODEL_SELECTION]: !isPlatformTauri(),

  // Model provider settings page management - disabled for web only
  [PlatformFeature.MODEL_PROVIDER_SETTINGS]: isPlatformTauri(),

  // Auto-enable MCP tool permissions - enabled for web platform
  [PlatformFeature.MCP_AUTO_APPROVE_TOOLS]: !isPlatformTauri(),

  // MCP servers settings page - disabled for web
  [PlatformFeature.MCP_SERVERS_SETTINGS]:
    isPlatformTauri() && !isPlatformIOS() && !isPlatformAndroid(),

  // Extensions settings page - disabled for web
  [PlatformFeature.EXTENSIONS_SETTINGS]:
    isPlatformTauri(),

  // Assistant functionality - disabled for web
  [PlatformFeature.ASSISTANTS]: isPlatformTauri(),

  // Shortcut
  [PlatformFeature.SHORTCUT]: !isPlatformIOS() && !isPlatformAndroid(),

}
