import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'cn.easyapitool.mobile',
  appName: 'DJYX_APITOOL',
  webDir: 'web',
  bundledWebRuntime: false,
  android: {
    allowMixedContent: false
  }
};

export default config;
