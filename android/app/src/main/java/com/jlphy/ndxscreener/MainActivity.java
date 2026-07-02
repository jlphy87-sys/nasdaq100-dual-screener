package com.jlphy.ndxscreener;

/* D17: PWA를 감싸는 최소 WebView 래퍼 — 의존성 0개(androidx 미사용).
 * - 화면은 GitHub Pages의 실제 웹앱을 그대로 로드 → 앱 셸이 바뀌어도 APK 재설치 불필요.
 * - 오프라인: 웹앱의 서비스워커+localStorage가 처리. WebView 는 domStorage 만 켜 주면 됨.
 *   메인 프레임 로드 자체가 실패한 최초 실행(캐시 전무)만 네이티브 오프라인 화면으로 안내.
 * - 외부 호스트 링크는 시스템 브라우저로 넘김 (앱 안에 가두지 않음).
 */

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;

public class MainActivity extends Activity {

    private static final String APP_URL = "https://jlphy87-sys.github.io/nasdaq100-dual-screener/";
    private static final String APP_HOST = "jlphy87-sys.github.io";

    private WebView web;
    private LinearLayout offlineView;
    private boolean loadFailed = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.parseColor("#0a0f1a"));

        web = new WebView(this);
        web.setBackgroundColor(Color.parseColor("#0a0f1a"));
        web.getSettings().setJavaScriptEnabled(true);   // 앱 자체가 JS 렌더링
        web.getSettings().setDomStorageEnabled(true);   // localStorage 캐시(오프라인 표시)의 전제
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(WebView v, WebResourceRequest req) {
                Uri uri = req.getUrl();
                if (APP_HOST.equals(uri.getHost())) return false; // 앱 내부 이동
                startActivity(new Intent(Intent.ACTION_VIEW, uri)); // 외부는 브라우저로
                return true;
            }

            @Override
            public void onPageStarted(WebView v, String url, android.graphics.Bitmap favicon) {
                loadFailed = false;
            }

            @Override
            public void onReceivedError(WebView v, WebResourceRequest req, WebResourceError err) {
                // 메인 프레임 실패(첫 실행 + 오프라인)만 네이티브 안내. 리소스 단위 실패는 무시.
                if (req.isForMainFrame()) {
                    loadFailed = true;
                    showOffline(true);
                }
            }

            @Override
            public void onPageFinished(WebView v, String url) {
                if (!loadFailed) showOffline(false);
            }
        });
        root.addView(web, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        offlineView = buildOfflineView();
        offlineView.setVisibility(View.GONE);
        root.addView(offlineView, new FrameLayout.LayoutParams(
                FrameLayout.LayoutParams.MATCH_PARENT, FrameLayout.LayoutParams.MATCH_PARENT));

        setContentView(root);

        if (savedInstanceState == null) {
            web.loadUrl(APP_URL);
        } else {
            web.restoreState(savedInstanceState);
        }
    }

    private LinearLayout buildOfflineView() {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setGravity(Gravity.CENTER);
        box.setBackgroundColor(Color.parseColor("#0a0f1a"));
        int pad = (int) (24 * getResources().getDisplayMetrics().density);
        box.setPadding(pad, pad, pad, pad);

        TextView title = new TextView(this);
        title.setText(R.string.offline_title);
        title.setTextColor(Color.parseColor("#eaf0fa"));
        title.setTextSize(18);
        title.setGravity(Gravity.CENTER);
        box.addView(title);

        TextView body = new TextView(this);
        body.setText(R.string.offline_body);
        body.setTextColor(Color.parseColor("#98a8c4"));
        body.setTextSize(13);
        body.setGravity(Gravity.CENTER);
        body.setPadding(0, pad / 2, 0, pad);
        box.addView(body);

        Button retry = new Button(this);
        retry.setText(R.string.retry);
        retry.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View v) {
                showOffline(false);
                web.loadUrl(APP_URL);
            }
        });
        box.addView(retry);
        return box;
    }

    private void showOffline(boolean on) {
        offlineView.setVisibility(on ? View.VISIBLE : View.GONE);
        web.setVisibility(on ? View.GONE : View.VISIBLE);
    }

    @Override
    public void onBackPressed() {
        if (web.canGoBack()) web.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }
}
