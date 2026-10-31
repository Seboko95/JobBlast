import { getDb } from "../../../db";
import { waitlistSignups } from "../../../db/schema";

const roles = new Set(["job-seeker", "organisation", "employer", "partner"]);

export async function POST(request: Request) {
  try {
    const payload = (await request.json()) as { email?: unknown; role?: unknown; company?: unknown };

    if (typeof payload.company === "string" && payload.company.trim()) {
      return Response.json({ message: "You’re on the JobBlast early-access list." });
    }

    const email = typeof payload.email === "string" ? payload.email.trim().toLowerCase() : "";
    const role = typeof payload.role === "string" && roles.has(payload.role) ? payload.role : "job-seeker";

    if (!email || email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      return Response.json({ error: "Enter a valid email address." }, { status: 400 });
    }

    const db = getDb();
    await db.insert(waitlistSignups).values({ email, role, source: "launch-site" }).onConflictDoNothing();

    return Response.json({ message: "You’re on the JobBlast early-access list." }, { status: 201 });
  } catch (error) {
    const detail = error instanceof Error ? error.message : "Unexpected error";
    console.error("Waitlist signup failed", detail);
    return Response.json({ error: "We could not save your place. Please try again." }, { status: 500 });
  }
}
