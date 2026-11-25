package cn.easyapitool.mobile;

import android.graphics.BitmapFactory;
import android.util.Base64;
import android.util.JsonReader;
import android.util.Xml;
import com.getcapacitor.JSObject;
import java.io.ByteArrayInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FilterInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URI;
import java.net.InetAddress;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.UUID;
import java.util.Set;
import org.xmlpull.v1.XmlPullParser;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

final class DirectImageApi {
    static final String GATEWAY = "https://imggen.djyx.pw/openai/v1";
    static final String OPENAI = "https://api.openai.com/v1";
    private static final String SVG_NS = "http://www.w3.org/2000/svg";
    private static final Set<String> UNSUPPORTED_SVG_TAGS = new java.util.HashSet<>(
        java.util.Arrays.asList("script", "foreignobject", "animate", "animatemotion", "animatetransform", "set")
    );

    private DirectImageApi() {}

    static URL publicReferenceURL(String value) throws Exception {
        URI uri = new URI(value);
        if (!"https".equalsIgnoreCase(uri.getScheme()) || uri.getHost() == null || uri.getUserInfo() != null
            || uri.getFragment() != null || value.length() > 4096 || uri.getHost().contains("%")
            || (uri.getPort() != -1 && uri.getPort() != 443)) throw new IOException("参考图仅允许公共 HTTPS 地址");
        for (InetAddress address : InetAddress.getAllByName(uri.getHost())) {
            byte[] bytes = address.getAddress();
            boolean reserved = bytes.length == 4 && (((bytes[0] & 255) == 100 && (bytes[1] & 255) >= 64 && (bytes[1] & 255) <= 127)
                || (bytes[0] & 255) >= 224 || (bytes[0] & 255) == 0 || (bytes[0] & 255) == 198 && ((bytes[1] & 255) == 18 || (bytes[1] & 255) == 19));
            reserved |= bytes.length == 16 && ((bytes[0] & 254) == 252);
            if (reserved || address.isAnyLocalAddress() || address.isLoopbackAddress() || address.isLinkLocalAddress()
                || address.isSiteLocalAddress() || address.isMulticastAddress()) throw new IOException("参考图地址不能指向内网或本机");
        }
        return uri.toURL();
    }

    static String certificateFailureCause(Throwable failure) {
        // Inspect a bounded cause chain; an ordinary TLS disconnect is still a transient network failure.
        for (int depth = 0; failure != null && depth < 8; depth++, failure = failure.getCause()) {
            String message = failure.getMessage();
            String detail = failure.getClass().getSimpleName() + (message == null ? "" : ": " + message);
            boolean certificate = failure instanceof java.security.cert.CertificateException
                || failure instanceof java.security.cert.CertPathValidatorException
                || failure instanceof javax.net.ssl.SSLPeerUnverifiedException;
            if (failure instanceof javax.net.ssl.SSLHandshakeException) {
                certificate |= detail.toLowerCase(java.util.Locale.ROOT).matches(
                    "(?s).*(certificate|certpath|cert_path|pkix|trust anchor|hostname|subject alternative|unknown_ca|bad_certificate).*"
                );
            }
            if (certificate) return detail.substring(0, Math.min(detail.length(), 800));
        }
        return "";
    }

    static String redact(String value, String secret) {
        String clean = value == null ? "" : value;
        if (secret != null && !secret.isEmpty()) clean = clean.replace(secret, "[API Key]");
        return clean.replaceAll("(?i)\\bsk-[A-Za-z0-9_\\-]{8,}", "[API Key]")
            .replaceAll("(?i)Bearer\\s+[^\\s\\\",}]+", "Bearer [redacted]")
            .replaceAll("(?i)(\\\"(?:api[_-]?key|authorization|password|secret)\\\"\\s*:\\s*)\\\"[^\\\"]*\\\"", "$1\\\"[redacted]\\\"")
            .replaceAll("(?i)(https?://)[^/\\s@]+@", "$1[redacted]@");
    }

    static String redactResponsePayload(String payload, String secret) {
        try {
            return redactResponseValue(new JSONObject(payload), secret).toString();
        } catch (JSONException invalid) {
            return redact(payload, secret);
        }
    }

    private static Object redactResponseValue(Object value, String secret) throws JSONException {
        if (value instanceof JSONObject object) {
            JSONObject safe = new JSONObject();
            var names = object.keys();
            while (names.hasNext()) {
                String name = names.next();
                Object item = object.get(name);
                if (item instanceof String && ("encrypted_content".equals(name) || "encryptedContent".equals(name))) {
                    // This opaque provider state must round-trip byte for byte.
                    // Regex redaction of a full JSON envelope could accidentally
                    // replace random key-like bytes inside encrypted compaction.
                    safe.put(name, item);
                } else if (name.matches("(?i)api[_-]?key|authorization|password|secret")) {
                    safe.put(name, "[redacted]");
                } else {
                    safe.put(name, redactResponseValue(item, secret));
                }
            }
            return safe;
        }
        if (value instanceof JSONArray array) {
            JSONArray safe = new JSONArray();
            for (int i = 0; i < array.length(); i++) safe.put(redactResponseValue(array.get(i), secret));
            return safe;
        }
        return value instanceof String text ? redact(text, secret) : value;
    }

    static String endpoint(String value) throws IOException {
        if (value == null || value.trim().isEmpty()) return "";
        String clean = value.trim().replaceAll("/+$", "");
        try {
            URI url = new URI(clean);
            if (!"https".equals(url.getScheme()) || url.getHost() == null || url.getPort() == 0
                || url.getPort() > 65535 || url.getRawAuthority().endsWith(":") || clean.length() > 2048
                || url.getRawUserInfo() != null || url.getRawQuery() != null || url.getRawFragment() != null) {
                throw new IllegalArgumentException();
            }
            if (url.getRawPath().isEmpty()) clean += "/v1";
            return isGateway(clean) ? GATEWAY : clean;
        } catch (Exception ignored) {
            throw new IOException("请输入有效的 HTTPS API 根地址");
        }
    }

    static boolean isGateway(String endpoint) {
        return GATEWAY.equalsIgnoreCase(endpoint)
            || "https://imggen.djyx.me/openai/v1".equalsIgnoreCase(endpoint)
            || "https://39.105.74.222/openai/v1".equalsIgnoreCase(endpoint)
            || "https://39.105.74.222:8443/openai/v1".equalsIgnoreCase(endpoint);
    }

    static void writeJson(HttpURLConnection connection, JSObject body) throws IOException {
        byte[] json = body.toString().getBytes(StandardCharsets.UTF_8);
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "application/json");
        connection.setFixedLengthStreamingMode(json.length);
        try (OutputStream output = connection.getOutputStream()) {
            output.write(json);
        }
    }

    static void writeEdit(HttpURLConnection connection, JSObject body, List<File> references) throws Exception {
        String boundary = "Mirra-" + UUID.randomUUID();
        connection.setDoOutput(true);
        connection.setRequestProperty("Content-Type", "multipart/form-data; boundary=" + boundary);
        connection.setChunkedStreamingMode(64 * 1024);
        try (OutputStream output = connection.getOutputStream()) {
            var names = body.keys();
            while (names.hasNext()) {
                String name = names.next();
                if (!name.matches("[a-z_]+")) throw new IOException("生图参数无效");
                text(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"" + name + "\"\r\n\r\n");
                text(output, body.get(name).toString() + "\r\n");
            }
            for (File file : references) {
                String name = file.getName().toLowerCase(java.util.Locale.ROOT);
                String mime = name.endsWith(".png") ? "image/png" : name.endsWith(".webp") ? "image/webp"
                    : name.endsWith(".jpg") || name.endsWith(".jpeg") ? "image/jpeg" : "";
                if (mime.isEmpty()) throw new IOException("参考图格式须为 PNG、JPEG 或 WebP");
                String suffix = "image/jpeg".equals(mime) ? "jpg" : mime.substring("image/".length());
                text(output, "--" + boundary + "\r\nContent-Disposition: form-data; name=\"image[]\"; "
                    + "filename=\"reference." + suffix + "\"\r\nContent-Type: " + mime + "\r\n\r\n");
                try (FileInputStream input = new FileInputStream(file)) {
                    byte[] buffer = new byte[64 * 1024];
                    for (int read; (read = input.read(buffer)) != -1;) output.write(buffer, 0, read);
                }
                text(output, "\r\n");
            }
            text(output, "--" + boundary + "--\r\n");
        }
    }

    private static void text(OutputStream output, String value) throws IOException {
        output.write(value.getBytes(StandardCharsets.UTF_8));
    }

    static byte[] readImage(InputStream response, long maxBytes) throws IOException {
        long maxJSON = (maxBytes + 2) / 3 * 4 + 1024 * 1024;
        InputStream bounded = new FilterInputStream(response) {
            private long consumed;

            @Override
            public int read(byte[] buffer, int offset, int length) throws IOException {
                int count = super.read(buffer, offset, length);
                if (count > 0 && (consumed += count) > maxJSON) throw new IOException("图片响应超出大小限制");
                return count;
            }
        };
        try (JsonReader reader = new JsonReader(new InputStreamReader(bounded, StandardCharsets.UTF_8))) {
            reader.beginObject();
            while (reader.hasNext()) {
                if (!"data".equals(reader.nextName())) {
                    reader.skipValue();
                    continue;
                }
                reader.beginArray();
                if (!reader.hasNext()) break;
                reader.beginObject();
                while (reader.hasNext()) {
                    if (!"b64_json".equals(reader.nextName())) {
                        reader.skipValue();
                        continue;
                    }
                    String encoded = reader.nextString();
                    if (encoded.length() > (maxBytes + 2) / 3 * 4) break;
                    try {
                        byte[] bytes = Base64.decode(encoded, Base64.DEFAULT);
                        if (bytes.length > 0 && bytes.length <= maxBytes) return bytes;
                    } catch (IllegalArgumentException ignored) {}
                    break;
                }
                break;
            }
        }
        throw new IOException("API 未返回有效的 Base64 图片");
    }

    record Image(byte[] bytes, String mime, int width, int height) {}

    static Image validateImage(byte[] bytes, String format) throws Exception {
        if ("svg".equals(format)) {
            validateSVG(bytes);
            return new Image(bytes, "image/svg+xml", 0, 0);
        }
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(bytes, 0, bytes.length, bounds);
        if (bounds.outMimeType == null || bounds.outWidth < 1 || bounds.outHeight < 1
            || !java.util.Arrays.asList("image/png", "image/jpeg", "image/webp", "image/gif").contains(bounds.outMimeType)) {
            throw new IOException("API 未返回支持的图片格式");
        }
        if ("gif".equals(format) && !"image/gif".equals(bounds.outMimeType)) {
            throw new IOException("API 未返回 GIF 动图");
        }
        return new Image(bytes, bounds.outMimeType, bounds.outWidth, bounds.outHeight);
    }

    private static void validateSVG(byte[] bytes) throws Exception {
        if (bytes.length > 8_000_000) throw new IOException("SVG 内容太大");
        XmlPullParser parser = Xml.newPullParser();
        parser.setFeature(XmlPullParser.FEATURE_PROCESS_NAMESPACES, true);
        parser.setInput(new ByteArrayInputStream(bytes), "UTF-8");
        boolean root = false;
        for (int token; (token = parser.nextToken()) != XmlPullParser.END_DOCUMENT;) {
            if (token == XmlPullParser.DOCDECL || token == XmlPullParser.PROCESSING_INSTRUCTION) {
                throw new IOException("SVG 含有无法预览的内容");
            }
            if (token != XmlPullParser.START_TAG) continue;
            String tag = parser.getName().toLowerCase(java.util.Locale.ROOT);
            if (parser.getDepth() == 1) {
                if (root || !"svg".equals(tag) || !java.util.Arrays.asList("", SVG_NS).contains(parser.getNamespace())) {
                    throw new IOException("API 未返回 SVG 内容");
                }
                root = true;
            }
            if (UNSUPPORTED_SVG_TAGS.contains(tag)) throw new IOException("SVG 含有无法预览的内容");
            for (int i = 0; i < parser.getAttributeCount(); i++) {
                String name = parser.getAttributeName(i).toLowerCase(java.util.Locale.ROOT);
                String value = parser.getAttributeValue(i).trim();
                if (name.startsWith("on") || ("href".equals(name) && !value.startsWith("#"))) {
                    throw new IOException("SVG 含有无法预览的内容");
                }
            }
        }
        if (!root) throw new IOException("API 未返回 SVG 内容");
    }
}
