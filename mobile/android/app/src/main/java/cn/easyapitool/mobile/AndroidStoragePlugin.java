package cn.easyapitool.mobile;

import android.app.Activity;
import android.content.ClipData;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Intent;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.provider.MediaStore;
import android.provider.OpenableColumns;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import androidx.activity.result.ActivityResult;
import androidx.core.content.FileProvider;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.ActivityCallback;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.security.KeyStore;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.JSONException;

@CapacitorPlugin(name = "AndroidStorage")
public class AndroidStoragePlugin extends Plugin {

    private static final int MAX_REFERENCE_COUNT = 16;
    private static final long MAX_ASSET_BYTES = 50L * 1024L * 1024L;
    private static final long MAX_UPDATE_BYTES = 200L * 1024L * 1024L;
    private static final int BUFFER_BYTES = 64 * 1024;
    private static final String KEYSTORE = "AndroidKeyStore";
    private static final String MASTER_KEY_ALIAS = "easyapitool.master.v1";
    private static final String KEY_PREFS = "easyapitool.secure.keys";
    private static final String SERVICE_PREFS = "easyapitool.secure.service";
    private final ExecutorService ioExecutor = Executors.newSingleThreadExecutor();
    private StorageDatabase database;

    @Override
    public void load() {
        database = new StorageDatabase();
        getSecureKeyStore().edit().clear().apply();
    }

    @PluginMethod
    public void pickImages(PluginCall call) {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("image/*");
        intent.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true);
        startActivityForResult(call, intent, "pickImagesResult");
    }

    @ActivityCallback
    private void pickImagesResult(PluginCall call, ActivityResult activityResult) {
        if (activityResult.getResultCode() != Activity.RESULT_OK || activityResult.getData() == null) {
            call.resolve(new JSObject().put("files", new JSArray()));
            return;
        }
        List<Uri> uris = selectedUris(activityResult.getData());
        JSArray files = new JSArray();
        List<String> warnings = new ArrayList<>();
        for (int index = 0; index < Math.min(MAX_REFERENCE_COUNT, uris.size()); index++) {
            Uri uri = uris.get(index);
            try {
                JSObject metadata = copyPickedImage(uri);
                if (metadata != null) files.put(metadata);
            } catch (IOException exception) {
                warnings.add("第 " + (index + 1) + " 张图片无法读取");
            }
        }
        if (uris.size() > MAX_REFERENCE_COUNT) {
            warnings.add("参考图片最多 16 张");
        }
        JSObject result = new JSObject();
        result.put("files", files);
        result.put("warning", String.join("；", warnings));
        call.resolve(result);
    }

    @PluginMethod
    public void uploadReference(PluginCall call) {
        String path = call.getString("path");
        String endpoint = call.getString("endpoint");
        String bearerToken = call.getString("bearerToken");
        if (path == null || endpoint == null || bearerToken == null) {
            call.reject("上传参数不完整");
            return;
        }
        File source = managedFile(path);
        if (source == null || !source.isFile()) {
            call.reject("参考图不存在");
            return;
        }
        String mimeType = mimeForPath(source.getName());
        ioExecutor.execute(() -> {
            try {
                JSObject response = uploadMultipart(source, mimeType, endpoint, bearerToken);
                call.resolve(response);
            } catch (Exception exception) {
                call.reject("参考图上传失败", exception);
            }
        });
    }

    @PluginMethod
    public void downloadAsset(PluginCall call) {
        String endpoint = call.getString("endpoint");
        String bearerToken = call.getString("bearerToken");
        String resourceId = safeId(call.getString("resourceId"));
        String sessionId = safeId(call.getString("sessionId"));
        String setId = safeId(call.getString("setId"));
        boolean preview = Boolean.TRUE.equals(call.getBoolean("preview", false));
        String mimeType = call.getString("mimeType", preview ? "image/jpeg" : "image/png");
        if (endpoint == null || bearerToken == null || resourceId.isEmpty()) {
            call.reject("下载参数不完整");
            return;
        }
        ioExecutor.execute(() -> {
            try {
                JSObject response = downloadToManagedFile(
                    endpoint,
                    bearerToken,
                    resourceId,
                    sessionId,
                    setId,
                    mimeType,
                    preview
                );
                call.resolve(response);
            } catch (Exception exception) {
                call.reject("图片下载失败", exception);
            }
        });
    }

    @PluginMethod
    public void getAppUpdateInfo(PluginCall call) {
        try {
            android.content.pm.PackageInfo packageInfo = getContext().getPackageManager()
                .getPackageInfo(getContext().getPackageName(), 0);
            long versionCode = Build.VERSION.SDK_INT >= Build.VERSION_CODES.P
                ? packageInfo.getLongVersionCode()
                : packageInfo.versionCode;
            call.resolve(new JSObject()
                .put("packageName", getContext().getPackageName())
                .put("versionCode", versionCode)
                .put("versionName", packageInfo.versionName == null ? "" : packageInfo.versionName)
                .put("buildTimestampMs", BuildConfig.BUILD_TIMESTAMP_MS));
        } catch (Exception exception) {
            call.reject("无法读取应用版本", exception);
        }
    }

    @PluginMethod
    public void downloadAppUpdate(PluginCall call) {
        String endpoint = call.getString("endpoint", "").trim();
        String expectedSha256 = call.getString("sha256", "").trim().toLowerCase();
        long expectedSize = call.getLong("sizeBytes", 0L);
        if (!endpoint.startsWith("https://") || expectedSha256.length() != 64 || expectedSize <= 0) {
            call.reject("更新下载参数无效");
            return;
        }
        ioExecutor.execute(() -> {
            File target = new File(new File(getContext().getCacheDir(), "updates"), "easyapitool-update.apk");
            try {
                if (!target.getParentFile().isDirectory() && !target.getParentFile().mkdirs()) {
                    throw new IOException("无法创建更新目录");
                }
                HttpURLConnection connection = openConnection(endpoint, null, "GET");
                int status = connection.getResponseCode();
                if (status < 200 || status >= 300) {
                    connection.disconnect();
                    throw new IOException("HTTP " + status);
                }
                long sizeBytes = copyLimited(connection.getInputStream(), target, MAX_UPDATE_BYTES);
                connection.disconnect();
                if (sizeBytes != expectedSize || !expectedSha256.equals(sha256File(target))) {
                    target.delete();
                    throw new IOException("更新安装包校验失败");
                }
                call.resolve(new JSObject()
                    .put("ok", true)
                    .put("path", target.getAbsolutePath())
                    .put("sizeBytes", sizeBytes)
                    .put("sha256", expectedSha256));
            } catch (Exception exception) {
                target.delete();
                call.reject("更新下载失败", exception);
            }
        });
    }

    @PluginMethod
    public void installAppUpdate(PluginCall call) {
        String path = call.getString("path", "");
        File source = new File(path);
        if (!isManagedUpdatePath(source) || !source.isFile()) {
            call.reject("更新安装包不存在");
            return;
        }
        try {
            Uri contentUri = FileProvider.getUriForFile(
                getContext(),
                getContext().getPackageName() + ".fileprovider",
                source
            );
            Intent intent = new Intent(Intent.ACTION_VIEW);
            intent.setDataAndType(contentUri, "application/vnd.android.package-archive");
            intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve(new JSObject().put("ok", true));
        } catch (Exception exception) {
            call.reject("无法打开系统安装器", exception);
        }
    }

    @PluginMethod
    public void listAssets(PluginCall call) {
        call.resolve(new JSObject().put("assets", database.list(safeId(call.getString("sessionId")))));
    }

    @PluginMethod
    public void deleteAsset(PluginCall call) {
        String resourceId = safeId(call.getString("resourceId"));
        for (File path : database.pathsFor(resourceId)) {
            if (path != null && isManagedPath(path)) path.delete();
        }
        database.delete(resourceId);
        call.resolve(new JSObject().put("resourceId", resourceId));
    }

    @PluginMethod
    public void importReferenceDataUrl(PluginCall call) {
        String dataUrl = call.getString("dataUrl", "");
        String name = call.getString("name", "reference.png");
        try {
            int comma = dataUrl.indexOf(',');
            if (!dataUrl.startsWith("data:image/") || comma < 0 || !dataUrl.substring(0, comma).contains(";base64")) {
                throw new IOException("只支持 PNG、JPEG 或 WebP 图片");
            }
            String mimeType = dataUrl.substring(5, dataUrl.indexOf(';'));
            if (suffixForMime(mimeType) == null) throw new IOException("仅支持 PNG、JPEG 或 WebP 图片");
            byte[] bytes = Base64.decode(dataUrl.substring(comma + 1), Base64.DEFAULT);
            if (bytes.length > MAX_ASSET_BYTES) throw new IOException("图片超过 50 MB");
            call.resolve(saveReferenceBytes(bytes, name, mimeType));
        } catch (Exception exception) {
            call.resolve(new JSObject().put("ok", false).put("error", exception.getMessage()));
        }
    }

    @PluginMethod
    public void readAsset(PluginCall call) {
        File source = managedFile(call.getString("path"));
        if (source == null || !source.isFile()) {
            call.reject("图片不存在");
            return;
        }
        try {
            byte[] bytes;
            try (InputStream input = new BufferedInputStream(new FileInputStream(source));
                 java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream()) {
                copy(input, output, MAX_ASSET_BYTES);
                bytes = output.toByteArray();
            }
            if (bytes.length > MAX_ASSET_BYTES) throw new IOException("图片超过 50 MB");
            String mimeType = mimeForPath(source.getName());
            call.resolve(new JSObject()
                .put("ok", true)
                .put("path", source.getAbsolutePath())
                .put("dataUrl", "data:" + mimeType + ";base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)));
        } catch (Exception exception) {
            call.reject("图片读取失败", exception);
        }
    }

    @PluginMethod
    public void saveAssetToGallery(PluginCall call) {
        File source = managedFile(call.getString("path"));
        if (source == null || !source.isFile()) {
            call.reject("图片不存在");
            return;
        }
        String mimeType = call.getString("mimeType", mimeForPath(source.getName()));
        String displayName = call.getString("name", source.getName());
        ContentResolver resolver = getContext().getContentResolver();
        ContentValues values = new ContentValues();
        values.put(MediaStore.Images.Media.DISPLAY_NAME, displayName);
        values.put(MediaStore.Images.Media.MIME_TYPE, mimeType);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            values.put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_DCIM + "/API_TOOLS");
            values.put(MediaStore.Images.Media.IS_PENDING, 1);
        }
        Uri target = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values);
        if (target == null) {
            call.reject("无法创建系统图片文件");
            return;
        }
        try (InputStream input = new BufferedInputStream(new FileInputStream(source));
             OutputStream output = new BufferedOutputStream(resolver.openOutputStream(target))) {
            if (output == null) throw new IOException("无法打开系统图片文件");
            copy(input, output, MAX_ASSET_BYTES);
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.Images.Media.IS_PENDING, 0);
                resolver.update(target, ready, null, null);
            }
            call.resolve(new JSObject()
                .put("ok", true)
                .put("uri", target.toString())
                .put("name", displayName)
                .put("path", "DCIM/API_TOOLS/" + displayName));
        } catch (Exception exception) {
            resolver.delete(target, null, null);
            call.reject("图片保存失败", exception);
        }
    }

    @PluginMethod
    public void listSecureKeys(PluginCall call) {
        try {
            call.resolve(new JSObject().put("keys", secureKeysMetadata()));
        } catch (Exception exception) {
            call.reject("无法读取安全密钥", exception);
        }
    }

    @PluginMethod
    public void saveSecureKey(PluginCall call) {
        String keyId = safeId(call.getString("keyId"));
        String name = call.getString("name", "API Key").trim();
        String value = call.getString("value");
        if (keyId.isEmpty() || name.isEmpty() || value == null || value.isEmpty()) {
            call.reject("安全密钥参数不完整");
            return;
        }
        try {
            database.upsertKey(keyId, name, encrypt(value));
            call.resolve(new JSObject().put("keyId", keyId));
        } catch (Exception exception) {
            call.reject("无法保存安全密钥", exception);
        }
    }

    @PluginMethod
    public void readSecureKey(PluginCall call) {
        String keyId = safeId(call.getString("keyId"));
        if (keyId.isEmpty()) {
            call.reject("安全密钥编号无效");
            return;
        }
        try {
            JSObject storedKey = database.key(keyId);
            if (storedKey == null) throw new IOException("安全密钥不存在");
            JSObject result = new JSObject();
            result.put("keyId", keyId);
            result.put("name", storedKey.getString("name", "API Key"));
            result.put("value", decrypt(storedKey.getString("encrypted", "")));
            call.resolve(result);
        } catch (Exception exception) {
            call.reject("无法读取安全密钥", exception);
        }
    }

    @PluginMethod
    public void deleteSecureKey(PluginCall call) {
        String keyId = safeId(call.getString("keyId"));
        if (keyId.isEmpty()) {
            call.reject("安全密钥编号无效");
            return;
        }
        database.deleteKey(keyId);
        call.resolve(new JSObject().put("keyId", keyId));
    }

    @PluginMethod
    public void saveServiceConfig(PluginCall call) {
        String serviceUrl = call.getString("serviceUrl", "").trim().replaceAll("/+$", "");
        String bearerToken = call.getString("bearerToken", "").trim();
        if (!serviceUrl.startsWith("https://")) {
            call.reject("服务地址必须使用 HTTPS");
            return;
        }
        if (bearerToken.isEmpty()) {
            call.reject("设备访问令牌不能为空");
            return;
        }
        try {
            getServiceStore().edit()
                .putString("serviceUrl", serviceUrl)
                .putString("bearerToken", encrypt(bearerToken))
                .apply();
            call.resolve(new JSObject().put("ok", true));
        } catch (Exception exception) {
            call.reject("无法保存设备连接配置", exception);
        }
    }

    @PluginMethod
    public void getOrCreateDeviceId(PluginCall call) {
        String deviceId = getServiceStore().getString("deviceId", "");
        if (deviceId.isEmpty()) {
            deviceId = UUID.randomUUID().toString();
            getServiceStore().edit().putString("deviceId", deviceId).apply();
        }
        call.resolve(new JSObject().put("deviceId", deviceId));
    }

    @PluginMethod
    public void readServiceConfig(PluginCall call) {
        try {
            String serviceUrl = getServiceStore().getString("serviceUrl", "");
            String encryptedToken = getServiceStore().getString("bearerToken", "");
            if (serviceUrl.isEmpty() || encryptedToken.isEmpty()) {
                throw new IOException("设备尚未完成服务配对");
            }
            call.resolve(new JSObject()
                .put("serviceUrl", serviceUrl)
                .put("bearerToken", decrypt(encryptedToken)));
        } catch (Exception exception) {
            call.reject("无法读取设备连接配置", exception);
        }
    }

    @PluginMethod
    public void clearServiceConfig(PluginCall call) {
        String deviceId = getServiceStore().getString("deviceId", "");
        getServiceStore().edit().clear().putString("deviceId", deviceId).apply();
        call.resolve(new JSObject().put("ok", true));
    }

    @Override
    protected void handleOnDestroy() {
        ioExecutor.shutdownNow();
        if (database != null) database.close();
        super.handleOnDestroy();
    }

    private List<Uri> selectedUris(Intent intent) {
        List<Uri> uris = new ArrayList<>();
        ClipData clipData = intent.getClipData();
        if (clipData != null) {
            for (int index = 0; index < clipData.getItemCount(); index++) {
                uris.add(clipData.getItemAt(index).getUri());
            }
        } else if (intent.getData() != null) {
            uris.add(intent.getData());
        }
        return uris;
    }

    private android.content.SharedPreferences getSecureKeyStore() {
        return getContext().getSharedPreferences(KEY_PREFS, android.content.Context.MODE_PRIVATE);
    }

    private android.content.SharedPreferences getServiceStore() {
        return getContext().getSharedPreferences(SERVICE_PREFS, android.content.Context.MODE_PRIVATE);
    }

    private JSArray secureKeysMetadata() {
        return database.listKeys();
    }

    private SecretKey getMasterKey() throws Exception {
        KeyStore keyStore = KeyStore.getInstance(KEYSTORE);
        keyStore.load(null);
        if (keyStore.containsAlias(MASTER_KEY_ALIAS)) {
            return ((KeyStore.SecretKeyEntry) keyStore.getEntry(MASTER_KEY_ALIAS, null)).getSecretKey();
        }
        KeyGenerator generator = KeyGenerator.getInstance("AES", KEYSTORE);
        generator.init(new KeyGenParameterSpec.Builder(
            MASTER_KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build());
        return generator.generateKey();
    }

    private String encrypt(String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, getMasterKey());
        byte[] iv = cipher.getIV();
        byte[] encrypted = cipher.doFinal(value.getBytes(StandardCharsets.UTF_8));
        return Base64.encodeToString(iv, Base64.NO_WRAP) + "." + Base64.encodeToString(encrypted, Base64.NO_WRAP);
    }

    private String decrypt(String encoded) throws Exception {
        String[] parts = encoded.split("\\.", 2);
        if (parts.length != 2) throw new IOException("安全密钥密文无效");
        byte[] iv = Base64.decode(parts[0], Base64.DEFAULT);
        byte[] encrypted = Base64.decode(parts[1], Base64.DEFAULT);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, getMasterKey(), new GCMParameterSpec(128, iv));
        return new String(cipher.doFinal(encrypted), StandardCharsets.UTF_8);
    }

    private JSObject copyPickedImage(Uri uri) throws IOException {
        ContentResolver resolver = getContext().getContentResolver();
        String mimeType = resolver.getType(uri);
        String suffix = suffixForMime(mimeType);
        if (suffix == null) return null;
        String displayName = displayName(uri, "reference" + suffix);
        File directory = new File(getContext().getFilesDir(), "easyapitool/references");
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IOException("无法创建参考图目录");
        }
        String resourceId = "local-ref-" + UUID.randomUUID().toString().replace("-", "");
        File destination = new File(directory, resourceId + suffix);
        long sizeBytes = copyLimited(resolver.openInputStream(uri), destination, MAX_ASSET_BYTES);
        return saveReferenceMetadata(destination, displayName, mimeType, sizeBytes);
    }

    private JSObject saveReferenceBytes(byte[] bytes, String name, String mimeType) throws IOException {
        String suffix = suffixForMime(mimeType);
        if (suffix == null) throw new IOException("不支持的图片格式");
        File directory = new File(getContext().getFilesDir(), "easyapitool/references");
        if (!directory.isDirectory() && !directory.mkdirs()) throw new IOException("无法创建参考图目录");
        String resourceId = "local-ref-" + UUID.randomUUID().toString().replace("-", "");
        File destination = new File(directory, resourceId + suffix);
        long sizeBytes = copyLimited(new ByteArrayInputStream(bytes), destination, MAX_ASSET_BYTES);
        return saveReferenceMetadata(destination, name, mimeType, sizeBytes);
    }

    private JSObject saveReferenceMetadata(File destination, String displayName, String mimeType, long sizeBytes) {
        String resourceId = destination.getName().substring(0, destination.getName().lastIndexOf('.'));
        ContentValues values = assetValues(
            resourceId,
            destination,
            false,
            "",
            "",
            mimeType,
            sizeBytes
        );
        database.upsert(resourceId, values);
        JSObject result = new JSObject();
        result.put("ok", true);
        result.put("resourceId", resourceId);
        result.put("path", destination.getAbsolutePath());
        result.put("uri", Uri.fromFile(destination).toString());
        result.put("name", displayName);
        result.put("sizeBytes", sizeBytes);
        result.put("mimeType", mimeType);
        return result;
    }

    private JSObject uploadMultipart(
        File source,
        String mimeType,
        String endpoint,
        String bearerToken
    ) throws IOException {
        String boundary = "----EasyApiTool" + UUID.randomUUID().toString().replace("-", "");
        HttpURLConnection connection = openConnection(endpoint, bearerToken, "POST");
        connection.setDoOutput(true);
        connection.setChunkedStreamingMode(BUFFER_BYTES);
        connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        try (OutputStream raw = new BufferedOutputStream(connection.getOutputStream())) {
            writeText(raw, "--" + boundary + "\r\n");
            writeText(
                raw,
                "Content-Disposition: form-data; name=\"file\"; filename=\"" + source.getName() + "\"\r\n"
            );
            writeText(raw, "Content-Type: " + mimeType + "\r\n\r\n");
            try (InputStream input = new BufferedInputStream(new FileInputStream(source))) {
                copy(input, raw, Long.MAX_VALUE);
            }
            writeText(raw, "\r\n--" + boundary + "--\r\n");
        }
        String response = readResponse(connection);
        connection.disconnect();
        try {
            return new JSObject(response);
        } catch (JSONException exception) {
            throw new IOException("服务端返回无效 JSON", exception);
        }
    }

    private JSObject downloadToManagedFile(
        String endpoint,
        String bearerToken,
        String resourceId,
        String sessionId,
        String setId,
        String mimeType,
        boolean preview
    ) throws IOException {
        String suffix = suffixForMime(mimeType);
        if (suffix == null) suffix = preview ? ".jpg" : ".png";
        File directory = new File(getContext().getFilesDir(), "easyapitool/assets");
        if (!directory.isDirectory() && !directory.mkdirs()) {
            throw new IOException("无法创建图片目录");
        }
        File target = new File(directory, resourceId + (preview ? "-preview" : "") + suffix);
        HttpURLConnection connection = openConnection(endpoint, bearerToken, "GET");
        int status = connection.getResponseCode();
        if (status < 200 || status >= 300) {
            connection.disconnect();
            throw new IOException("HTTP " + status);
        }
        long sizeBytes = copyLimited(connection.getInputStream(), target, MAX_ASSET_BYTES);
        connection.disconnect();
        database.upsert(
            resourceId,
            assetValues(resourceId, target, preview, sessionId, setId, mimeType, sizeBytes)
        );
        JSObject result = new JSObject();
        result.put("resourceId", resourceId);
        result.put("path", target.getAbsolutePath());
        result.put("uri", Uri.fromFile(target).toString());
        result.put("sizeBytes", sizeBytes);
        result.put("mimeType", mimeType);
        result.put("preview", preview);
        return result;
    }

    private HttpURLConnection openConnection(
        String endpoint,
        String bearerToken,
        String method
    ) throws IOException {
        URL url = new URL(endpoint);
        String protocol = url.getProtocol();
        if (!"https".equals(protocol) && !"http".equals(protocol)) {
            throw new IOException("仅允许 HTTP 或 HTTPS 服务地址");
        }
        HttpURLConnection connection = (HttpURLConnection) url.openConnection();
        connection.setRequestMethod(method);
        connection.setConnectTimeout(15_000);
        connection.setReadTimeout(15 * 60_000);
        if (bearerToken != null && !bearerToken.isEmpty()) {
            connection.setRequestProperty("Authorization", "Bearer " + bearerToken);
        }
        connection.setRequestProperty("Accept", "application/json, image/*");
        return connection;
    }

    private String readResponse(HttpURLConnection connection) throws IOException {
        int status = connection.getResponseCode();
        InputStream stream = status >= 200 && status < 300
            ? connection.getInputStream()
            : connection.getErrorStream();
        if (stream == null) throw new IOException("HTTP " + status);
        byte[] bytes;
        try (InputStream input = stream; java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream()) {
            copy(input, output, 2 * 1024 * 1024);
            bytes = output.toByteArray();
        }
        String body = new String(bytes, StandardCharsets.UTF_8);
        if (status < 200 || status >= 300) throw new IOException("HTTP " + status + ": " + body);
        return body;
    }

    private long copyLimited(InputStream input, File destination, long maximum) throws IOException {
        if (input == null) throw new IOException("无法读取文件");
        try (InputStream source = new BufferedInputStream(input); OutputStream output = new BufferedOutputStream(new FileOutputStream(destination))) {
            return copy(source, output, maximum);
        } catch (IOException exception) {
            destination.delete();
            throw exception;
        }
    }

    private long copy(InputStream input, OutputStream output, long maximum) throws IOException {
        byte[] buffer = new byte[BUFFER_BYTES];
        long total = 0;
        int read;
        while ((read = input.read(buffer)) != -1) {
            total += read;
            if (total > maximum) throw new IOException("文件超过大小限制");
            output.write(buffer, 0, read);
        }
        output.flush();
        return total;
    }

    private String sha256File(File source) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = new BufferedInputStream(new FileInputStream(source))) {
            byte[] buffer = new byte[BUFFER_BYTES];
            int read;
            while ((read = input.read(buffer)) != -1) digest.update(buffer, 0, read);
        }
        StringBuilder result = new StringBuilder(64);
        for (byte value : digest.digest()) result.append(String.format("%02x", value));
        return result.toString();
    }

    private void writeText(OutputStream output, String value) throws IOException {
        output.write(value.getBytes(StandardCharsets.UTF_8));
    }

    private ContentValues assetValues(
        String resourceId,
        File path,
        boolean preview,
        String sessionId,
        String setId,
        String mimeType,
        long sizeBytes
    ) {
        ContentValues values = new ContentValues();
        values.put("resource_id", resourceId);
        values.put(preview ? "preview_path" : "local_path", path.getAbsolutePath());
        values.put("session_id", sessionId);
        values.put("set_id", setId);
        values.put("mime_type", mimeType);
        values.put("size_bytes", sizeBytes);
        values.put("created_at", System.currentTimeMillis());
        return values;
    }

    private File managedFile(String path) {
        if (path == null) return null;
        File file = new File(path);
        return isManagedPath(file) ? file : null;
    }

    private boolean isManagedPath(File path) {
        try {
            File root = new File(getContext().getFilesDir(), "easyapitool").getCanonicalFile();
            File candidate = path.getCanonicalFile();
            return candidate.toPath().startsWith(root.toPath());
        } catch (IOException exception) {
            return false;
        }
    }

    private boolean isManagedUpdatePath(File path) {
        try {
            File root = new File(getContext().getCacheDir(), "updates").getCanonicalFile();
            File candidate = path.getCanonicalFile();
            return candidate.toPath().startsWith(root.toPath());
        } catch (IOException exception) {
            return false;
        }
    }

    private String displayName(Uri uri, String fallback) {
        try (Cursor cursor = getContext().getContentResolver().query(
            uri,
            new String[] { OpenableColumns.DISPLAY_NAME },
            null,
            null,
            null
        )) {
            if (cursor != null && cursor.moveToFirst()) {
                String value = cursor.getString(0);
                if (value != null && !value.isEmpty()) return value;
            }
        }
        return fallback;
    }

    private static String safeId(String value) {
        if (value == null) return "";
        String clean = value.replaceAll("[^A-Za-z0-9_-]", "");
        return clean.substring(0, Math.min(96, clean.length()));
    }

    private static String suffixForMime(String mimeType) {
        if ("image/png".equals(mimeType)) return ".png";
        if ("image/jpeg".equals(mimeType)) return ".jpg";
        if ("image/webp".equals(mimeType)) return ".webp";
        return null;
    }

    private static String mimeForPath(String name) {
        String lower = name.toLowerCase();
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) return "image/jpeg";
        if (lower.endsWith(".webp")) return "image/webp";
        return "image/png";
    }

    private class StorageDatabase extends SQLiteOpenHelper {

        StorageDatabase() {
            super(getContext(), "easyapitool-index.db", null, 2);
        }

        @Override
        public void onCreate(SQLiteDatabase db) {
            db.execSQL(
                "CREATE TABLE assets (" +
                "resource_id TEXT PRIMARY KEY," +
                "local_path TEXT NOT NULL DEFAULT ''," +
                "preview_path TEXT NOT NULL DEFAULT ''," +
                "session_id TEXT NOT NULL DEFAULT ''," +
                "set_id TEXT NOT NULL DEFAULT ''," +
                "mime_type TEXT NOT NULL," +
                "size_bytes INTEGER NOT NULL," +
                "created_at INTEGER NOT NULL)"
            );
            db.execSQL("CREATE INDEX idx_assets_session ON assets(session_id, created_at)");
            createKeyTable(db);
        }

        @Override
        public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
            if (oldVersion < 2) createKeyTable(db);
        }

        private void createKeyTable(SQLiteDatabase db) {
            db.execSQL(
                "CREATE TABLE IF NOT EXISTS api_keys (" +
                "key_id TEXT PRIMARY KEY," +
                "name TEXT NOT NULL," +
                "secret_encrypted TEXT NOT NULL," +
                "created_at INTEGER NOT NULL)"
            );
        }

        void upsertKey(String keyId, String name, String encrypted) {
            ContentValues values = new ContentValues();
            values.put("key_id", keyId);
            values.put("name", name);
            values.put("secret_encrypted", encrypted);
            values.put("created_at", System.currentTimeMillis());
            getWritableDatabase().insertWithOnConflict(
                "api_keys", null, values, SQLiteDatabase.CONFLICT_REPLACE
            );
        }

        JSObject key(String keyId) {
            try (Cursor cursor = getReadableDatabase().query(
                "api_keys",
                new String[] { "name", "secret_encrypted" },
                "key_id=?",
                new String[] { keyId },
                null,
                null,
                null,
                "1"
            )) {
                if (!cursor.moveToFirst()) return null;
                return new JSObject()
                    .put("name", cursor.getString(0))
                    .put("encrypted", cursor.getString(1));
            }
        }

        JSArray listKeys() {
            JSArray keys = new JSArray();
            try (Cursor cursor = getReadableDatabase().query(
                "api_keys",
                new String[] { "key_id", "name" },
                null,
                null,
                null,
                null,
                "created_at ASC"
            )) {
                while (cursor.moveToNext()) {
                    keys.put(new JSObject()
                        .put("id", cursor.getString(0))
                        .put("name", cursor.getString(1)));
                }
            }
            return keys;
        }

        void deleteKey(String keyId) {
            getWritableDatabase().delete("api_keys", "key_id=?", new String[] { keyId });
        }

        void upsert(String resourceId, ContentValues values) {
            SQLiteDatabase db = getWritableDatabase();
            db.insertWithOnConflict("assets", null, values, SQLiteDatabase.CONFLICT_IGNORE);
            db.update("assets", values, "resource_id=?", new String[] { resourceId });
        }

        File[] pathsFor(String resourceId) {
            try (Cursor cursor = getReadableDatabase().query(
                "assets",
                new String[] { "local_path", "preview_path" },
                "resource_id=?",
                new String[] { resourceId },
                null,
                null,
                null,
                "1"
            )) {
                if (!cursor.moveToFirst()) return new File[0];
                String local = cursor.getString(0);
                String preview = cursor.getString(1);
                return new File[] {
                    local.isEmpty() ? null : new File(local),
                    preview.isEmpty() ? null : new File(preview)
                };
            }
        }

        JSArray list(String sessionId) {
            JSArray assets = new JSArray();
            String selection = sessionId.isEmpty() ? null : "session_id=?";
            String[] arguments = sessionId.isEmpty() ? null : new String[] { sessionId };
            try (Cursor cursor = getReadableDatabase().query(
                "assets",
                new String[] {
                    "resource_id", "local_path", "preview_path", "session_id", "set_id",
                    "mime_type", "size_bytes", "created_at"
                },
                selection,
                arguments,
                null,
                null,
                "created_at DESC"
            )) {
                while (cursor.moveToNext()) {
                    JSObject asset = new JSObject();
                    asset.put("resourceId", cursor.getString(0));
                    asset.put("path", cursor.getString(1));
                    asset.put("previewPath", cursor.getString(2));
                    asset.put("sessionId", cursor.getString(3));
                    asset.put("setId", cursor.getString(4));
                    asset.put("mimeType", cursor.getString(5));
                    asset.put("sizeBytes", cursor.getLong(6));
                    asset.put("createdAt", cursor.getLong(7));
                    assets.put(asset);
                }
            }
            return assets;
        }

        void delete(String resourceId) {
            getWritableDatabase().delete("assets", "resource_id=?", new String[] { resourceId });
        }
    }
}
