import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | undefined;

// The only place that reads the project's address and publishable key.
export function supabase(): SupabaseClient {
  client ??= createClient(
    String(import.meta.env.VITE_SUPABASE_URL),
    String(import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY),
  );
  return client;
}
