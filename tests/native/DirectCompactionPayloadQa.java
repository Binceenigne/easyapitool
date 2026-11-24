package cn.easyapitool.mobile;

import org.json.JSONObject;

/** Run with the actual DirectImageApi class and a JVM org.json implementation. */
public final class DirectCompactionPayloadQa {
    private static void check(boolean valid, String label) {
        if (!valid) throw new AssertionError(label);
    }

    public static void main(String[] args) throws Exception {
        String opaque = "opaque/sk-looks-like-a-key-123456789/Bearer abc+state==\n".repeat(60000);
        String secret = "sk-fixture-sensitive-123456789";
        JSONObject input = new JSONObject().put("status", "completed")
            .put("output", new org.json.JSONArray().put(new JSONObject().put("type", "compaction")
                .put("id", "cmp-fixture").put("encrypted_content", opaque)))
            .put("error", new JSONObject().put("message", "fixture " + secret + " Bearer leak-credential https://user:pass@example.test")
                .put("password", "sensitive-password"));
        JSONObject result = new JSONObject(DirectImageApi.redactResponsePayload(input.toString(), secret));
        check(result.getJSONArray("output").getJSONObject(0).getString("encrypted_content").equals(opaque),
            "large encrypted compaction must remain byte-for-byte intact");
        String error = result.getJSONObject("error").toString();
        check(!error.contains(secret) && !error.contains("leak-credential") && !error.contains("user:pass")
            && !error.contains("sensitive-password"), "ordinary error credentials must remain redacted");
        check(DirectImageApi.redactResponsePayload("invalid " + secret, secret).contains("[API Key]"),
            "invalid provider JSON retains safe error redaction");
        System.out.println("Native SDK payload: >3MB opaque compaction round-trip, structured credential redaction and invalid JSON fallback passed");
    }
}
