import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'cn.easyapitool.mobile',
  appName: 'Mirra(觅然)',
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
