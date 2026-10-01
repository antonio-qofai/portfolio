// Applies db/schema.sql. Run with: npm run db:migrate
// Uses the unpooled connection, since schema changes should not go through pgbouncer.

import { readFileSync } from "node:fs";
import { neon } from "@neondatabase/serverless";

const url = process.env.DATABASE_URL_UNPOOLED ?? process.env.DATABASE_URL;
if (!url) throw new Error("Set DATABASE_URL_UNPOOLED or DATABASE_URL in .env");

const sql = neon(url);
const statements = readFileSync(new URL("../db/schema.sql", import.meta.url), "utf8")
  .split(/;\s*$/m)
  .map(s => s.replace(/^\s*--.*$/gm, "").trim())
  .filter(Boolean);

for (const statement of statements) await sql.query(statement);
console.log(`Applied ${statements.length} statements.`);
