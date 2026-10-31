import Image from "next/image";
import { EarlyAccessForm } from "./EarlyAccessForm";

const features = [
  {
    number: "01",
    title: "Smart job matching",
    copy: "JobBlast reads your target roles, location, keywords and resume to surface the opportunities worth your time.",
  },
  {
    number: "02",
    title: "Application-ready materials",
    copy: "Create tailored cover letters, suggested answers, gap analysis, DOCX letters and organised application packs.",
  },
  {
    number: "03",
    title: "A pipeline that remembers",
    copy: "Save roles, mark applications, review stale opportunities and get reminders before important deadlines pass.",
  },
  {
    number: "04",
    title: "Public service, simplified",
    copy: "Discover public-sector opportunities and keep Z83 guidance, forms and required-document support close at hand.",
  },
  {
    number: "05",
    title: "Resume-powered fit scores",
    copy: "See why a role matches, where your evidence is strong and what your application still needs to address.",
  },
  {
    number: "06",
    title: "You stay in control",
    copy: "JobBlast prepares and organises. You review every application and choose when and where to submit it.",
  },
];

const steps = [
  ["01", "Build your profile", "Set your target roles, locations and preferences, then upload your active resume."],
  ["02", "Scan and shortlist", "Let JobBlast search enabled sources, score vacancies and organise the strongest matches."],
  ["03", "Prepare and apply", "Generate your materials, check deadlines and submit manually with confidence."],
];

export default function Home() {
  return (
    <main>
      <a className="skip-link" href="#main-content">Skip to content</a>

      <header className="site-header">
        <a className="brand" href="#top" aria-label="JobBlast home">
          <span className="brand-mark">JB</span>
          <span>JOBBLAST</span>
        </a>
        <nav aria-label="Main navigation">
          <a href="#how-it-works">How it works</a>
          <a href="#features">Features</a>
          <a href="#organisations">For organisations</a>
        </nav>
        <a className="button button-small" href="#early-access">Join early access</a>
      </header>

      <section className="hero" id="top">
        <div className="hero-glow hero-glow-one" />
        <div className="hero-glow hero-glow-two" />
        <div className="hero-copy" id="main-content">
          <div className="eyebrow"><span /> Coming soon · Built for South Africa</div>
          <h1>A smarter job search, <em>straight from Telegram.</em></h1>
          <p className="hero-lede">
            Find better-fit opportunities, prepare tailored application materials and keep every deadline organised—without giving up control of your applications.
          </p>
          <div className="hero-actions">
            <a className="button" href="#early-access">Get early access <span aria-hidden="true">↗</span></a>
            <a className="text-link" href="#how-it-works">See how it works <span aria-hidden="true">↓</span></a>
          </div>
          <div className="trust-row" aria-label="Product principles">
            <span>✓ Manual apply</span>
            <span>✓ Privacy-first</span>
            <span>✓ No guaranteed outcomes</span>
          </div>
        </div>

        <div className="hero-visual" aria-label="JobBlast coming soon preview">
          <div className="poster-shell">
            <Image
              src="/jobblast-poster.png"
              alt="JobBlast coming soon poster showing smart matches, cover letters, application packs, job tracking, Z83 support and deadline alerts"
              width={1003}
              height={1568}
              priority
            />
          </div>
          <div className="floating-card match-card">
            <span className="mini-label">TOP MATCH</span>
            <strong>82% fit score</strong>
            <span>Role aligned to your profile</span>
          </div>
          <div className="floating-card deadline-card">
            <span className="pulse-dot" />
            <div><strong>Deadline alert</strong><span>3 days remaining</span></div>
          </div>
        </div>
      </section>

      <section className="proof-strip" aria-label="Key JobBlast capabilities">
        <span>SMART MATCHES</span><i />
        <span>COVER LETTERS</span><i />
        <span>APPLICATION PACKS</span><i />
        <span>JOB TRACKING</span><i />
        <span>Z83 SUPPORT</span>
      </section>

      <section className="section how-section" id="how-it-works">
        <div className="section-heading">
          <div><span className="kicker">HOW IT WORKS</span><h2>From scattered searching<br />to a clear next move.</h2></div>
          <p>JobBlast brings discovery, preparation and tracking into one focused Telegram workflow.</p>
        </div>
        <div className="steps-grid">
          {steps.map(([number, title, copy]) => (
            <article className="step-card" key={number}>
              <span className="step-number">{number}</span>
              <div className="step-icon" aria-hidden="true">{number === "01" ? "◎" : number === "02" ? "⌕" : "✓"}</div>
              <h3>{title}</h3>
              <p>{copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="section features-section" id="features">
        <div className="section-heading compact">
          <div><span className="kicker">BUILT FOR THE REAL SEARCH</span><h2>Less admin.<br />Better applications.</h2></div>
          <p>Everything here reflects JobBlast&apos;s current manual-apply workflow—no mass application claims and no shortcuts that put your reputation at risk.</p>
        </div>
        <div className="feature-grid">
          {features.map((feature) => (
            <article className="feature-card" key={feature.number}>
              <span>{feature.number}</span>
              <h3>{feature.title}</h3>
              <p>{feature.copy}</p>
              <div className="feature-line" />
            </article>
          ))}
        </div>
      </section>

      <section className="section control-section">
        <div className="control-panel">
          <div className="control-copy">
            <span className="kicker">A BETTER KIND OF AUTOMATION</span>
            <h2>The bot does the busywork.<br /><em>You make the decision.</em></h2>
            <p>JobBlast helps you find, understand and prepare for opportunities. It never pretends that more applications automatically mean better outcomes.</p>
            <ul>
              <li><span>✓</span> Review every vacancy before applying</li>
              <li><span>✓</span> Edit every generated document</li>
              <li><span>✓</span> Keep your job-search pipeline organised</li>
            </ul>
          </div>
          <div className="telegram-mockup" aria-label="Example JobBlast Telegram conversation">
            <div className="telegram-top"><span className="mock-avatar">JB</span><div><strong>JobBlast</strong><small>Manual Apply Assistant</small></div><span className="online-dot" /></div>
            <div className="chat-body">
              <div className="chat-bubble bot-bubble"><strong>3 strong matches found</strong><span>Your top role scored 82 against your resume and preferences.</span><small>08:03</small></div>
              <div className="job-preview"><span className="mini-label">TOP MATCH</span><strong>Medical Sales Representative</strong><p>Gauteng · Score 82 · Closes in 6 days</p><div><span>View role</span><span>Prepare pack</span></div></div>
              <div className="chat-bubble bot-bubble last"><strong>Application pack ready</strong><span>Cover letter, gap analysis and checklist prepared for your review.</span><small>08:04</small></div>
            </div>
          </div>
        </div>
      </section>

      <section className="section plans-section">
        <div className="section-heading centered">
          <div><span className="kicker">LAUNCH PLANS</span><h2>Start free. Upgrade when<br />the search gets serious.</h2></div>
          <p>Early-access members will be first to receive launch pricing and Career Sprint offers.</p>
        </div>
        <div className="plans-grid">
          <article className="plan-card">
            <span className="plan-name">FREE</span><h3>Get organised</h3><p className="price">R0 <small>/ always</small></p>
            <ul><li>Job discovery and matching</li><li>Core application pipeline</li><li>Public service and Z83 support</li><li>Limited application materials</li></ul>
            <a className="plan-link" href="#early-access">Join the launch list</a>
          </article>
          <article className="plan-card featured-plan">
            <div className="popular">MOST FLEXIBLE</div>
            <span className="plan-name">CAREER SPRINT</span><h3>Move with momentum</h3><p className="price">7 days <small>/ one-off</small></p>
            <ul><li>More tailored application packs</li><li>Gap analysis and fit insights</li><li>Deadline and follow-up alerts</li><li>No long subscription required</li></ul>
            <a className="button plan-button" href="#early-access">Get launch access ↗</a>
          </article>
          <article className="plan-card">
            <span className="plan-name">PLUS</span><h3>Run the full search</h3><p className="price">Monthly <small>/ cancel anytime</small></p>
            <ul><li>Higher scan and material limits</li><li>Advanced pipeline views</li><li>Exports and priority features</li><li>New premium tools as they launch</li></ul>
            <a className="plan-link" href="#early-access">Be first to know</a>
          </article>
        </div>
        <p className="pricing-note">Final pricing and limits will be confirmed before launch.</p>
      </section>

      <section className="section org-section" id="organisations">
        <div className="org-copy"><span className="kicker">FOR COLLEGES, NGOS & TRAINING PROVIDERS</span><h2>Give a whole cohort<br />a clearer job search.</h2><p>JobBlast can support youth-employment programmes and graduate cohorts with guided onboarding, practical application workflows and organisation-level reporting.</p><a className="text-link light" href="#early-access">Request a pilot conversation ↗</a></div>
        <div className="org-stats"><div><strong>100</strong><span>seeker pilot-ready</span></div><div><strong>1</strong><span>consistent workflow</span></div><div><strong>0</strong><span>auto-apply spam</span></div></div>
      </section>

      <section className="section early-section" id="early-access">
        <div className="early-card">
          <div className="early-copy"><span className="kicker">EARLY ACCESS</span><h2>Be there when<br />JobBlast launches.</h2><p>Join the list for launch access, founding-member offers and product updates. No spam—just the important milestones.</p></div>
          <EarlyAccessForm />
        </div>
      </section>

      <footer>
        <a className="brand footer-brand" href="#top"><span className="brand-mark">JB</span><span>JOBBLAST</span></a>
        <p>A manual-apply job assistant built to help people search with focus and apply with confidence.</p>
        <div><a href="#features">Features</a><a href="#organisations">Organisations</a><a href="#early-access">Early access</a></div>
        <small>© 2026 JobBlast · Coming soon on Telegram · No guaranteed employment outcomes.</small>
      </footer>
    </main>
  );
}
