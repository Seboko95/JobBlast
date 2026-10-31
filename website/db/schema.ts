import { sql } from "drizzle-orm";
import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";

export const waitlistSignups = sqliteTable("waitlist_signups", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  email: text("email").notNull().unique(),
  role: text("role").notNull().default("job-seeker"),
  source: text("source").notNull().default("launch-site"),
  status: text("status").notNull().default("waiting"),
  createdAt: text("created_at").notNull().default(sql`CURRENT_TIMESTAMP`),
});
