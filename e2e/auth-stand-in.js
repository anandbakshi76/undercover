// A stand-in for Supabase Auth on port 8004. It signs tokens with a key of its
// own, serves the matching public keys and answers the browser's cross-origin
// checks, so the end-to-end tests sign in without Supabase.
import { createServer } from "node:http";
import { createSign, generateKeyPairSync } from "node:crypto";

const PASSWORD = "e2e-password";
const { publicKey, privateKey } = generateKeyPairSync("rsa", {
  modulusLength: 2048,
});
const jwk = {
  ...publicKey.export({ format: "jwk" }),
  kid: "stand-in",
  alg: "RS256",
  use: "sig",
};

const encode = (value) => Buffer.from(value).toString("base64url");

function user(email) {
  return {
    id: `user-${email}`,
    aud: "authenticated",
    role: "authenticated",
    email,
    app_metadata: {},
    user_metadata: {},
    created_at: "2026-01-01T00:00:00Z",
  };
}

function session(email) {
  const issued = Math.floor(Date.now() / 1000);
  const header = encode(
    JSON.stringify({ alg: "RS256", typ: "JWT", kid: "stand-in" }),
  );
  const payload = encode(
    JSON.stringify({
      sub: `user-${email}`,
      email,
      aud: "authenticated",
      role: "authenticated",
      iat: issued,
      exp: issued + 3600,
    }),
  );
  const signature = createSign("RSA-SHA256")
    .update(`${header}.${payload}`)
    .sign(privateKey)
    .toString("base64url");
  return {
    access_token: `${header}.${payload}.${signature}`,
    token_type: "bearer",
    expires_in: 3600,
    expires_at: issued + 3600,
    refresh_token: `refresh-${email}`,
    user: user(email),
  };
}

function reply(response, status, body) {
  response.writeHead(status, {
    "Content-Type": "application/json",
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers":
      "authorization, apikey, content-type, x-client-info, x-supabase-api-version",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  });
  response.end(body === undefined ? undefined : JSON.stringify(body));
}

createServer((request, response) => {
  const url = new URL(request.url ?? "/", "http://127.0.0.1:8004");
  if (request.method === "OPTIONS") {
    reply(response, 204);
    return;
  }
  if (url.pathname === "/auth/v1/.well-known/jwks.json") {
    reply(response, 200, { keys: [jwk] });
    return;
  }
  let text = "";
  request.on("data", (chunk) => {
    text += chunk;
  });
  request.on("end", () => {
    const body = text ? JSON.parse(text) : {};
    if (url.pathname === "/auth/v1/token") {
      const grant = url.searchParams.get("grant_type");
      if (grant === "refresh_token") {
        reply(response, 200, session(String(body.refresh_token).slice(8)));
      } else if (body.password === PASSWORD) {
        reply(response, 200, session(body.email));
      } else {
        reply(response, 400, {
          code: 400,
          error_code: "invalid_credentials",
          msg: "Invalid login credentials",
        });
      }
      return;
    }
    if (url.pathname === "/auth/v1/logout") {
      reply(response, 204);
      return;
    }
    reply(response, 404, { msg: "Not found" });
  });
}).listen(8004, "127.0.0.1");
