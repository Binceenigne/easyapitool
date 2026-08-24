package cn.easyapitool.mobile;

import android.os.Bundle;
import android.view.View;
import android.view.WindowManager;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

	@Override
	protected void onCreate(Bundle savedInstanceState) {
		registerPlugin(AndroidStoragePlugin.class);
		super.onCreate(savedInstanceState);
		getWindow().setSoftInputMode(
			WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
				| WindowManager.LayoutParams.SOFT_INPUT_STATE_UNSPECIFIED
		);
		View webView = getBridge().getWebView();
		ViewCompat.setOnApplyWindowInsetsListener(webView, (view, insets) -> {
			int keyboardBottom = insets.isVisible(WindowInsetsCompat.Type.ime())
				? insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
				: 0;
			view.setPadding(0, 0, 0, keyboardBottom);
			return insets;
		});
		ViewCompat.requestApplyInsets(webView);
		applyImmersiveMode();
	}

	@Override
	public void onWindowFocusChanged(boolean hasFocus) {
		super.onWindowFocusChanged(hasFocus);
		if (hasFocus) applyImmersiveMode();
	}

	private void applyImmersiveMode() {
		WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
		WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
		if (controller == null) return;
		controller.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
		controller.hide(WindowInsetsCompat.Type.systemBars());
	}
}
