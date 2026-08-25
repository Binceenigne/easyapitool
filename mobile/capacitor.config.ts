import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'cn.easyapitool.mobile',
  appName: 'DJYX_IMGenTool',
  webDir: 'web',
  bundledWebRuntime: false,
  android: {
    allowMixedContent: false
  },
  plugins: {
    Keyboard: {
      resizeOnFullScreen: true
    }
  }
};

export default config;
