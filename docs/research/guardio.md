# Guardio — Research Report

**Subject:** Guardio Ltd. (guard.io) — consumer cybersecurity, Tel Aviv, Israel
**Date:** 9 August 2026
**Scope:** Company, product, funding, threat research output, market position, and risks.

> **Note on scope:** this is a standalone research document. It is unrelated to the
> `pwb_toolbox` package and nothing under `pwb_toolbox/` imports or depends on it.

---

## 1. Executive summary

Guardio is a Tel Aviv–based consumer cybersecurity company built around a browser
extension and, more recently, a mobile app. It sells subscription protection against
phishing, scam sites, malicious extensions, and credential exposure — deliberately
targeting consumers rather than enterprises.

The short version:

- **Real product, real research, real growth.** ~1.5M users, three consecutive years of
  >100% ARR growth, and an $80M Series B in November 2025. Its research arm, Guardio Labs,
  has produced genuinely well-regarded work (a Microsoft Edge CVE, the SubdoMailing
  campaign, the "Scamlexity" agentic-browser findings).
- **But the commercial packaging draws consistent criticism.** Fear-based marketing,
  annual plans advertised at monthly-equivalent prices, and difficult cancellations
  are recurring complaint themes across BBB, Trustpilot's lower tail, and consumer forums.
- **The biggest technical gap is verification.** Guardio has never been submitted to
  AV-TEST, AV-Comparatives, or SE Labs. For a security product, the absence of
  independent lab certification is the single most material caveat in this report.
- **The strategic bet is AI.** Guardio has pivoted its positioning toward protecting AI
  browsers and agents, and its first B2B deal (Lovable) embeds its detection engine into
  an AI code-generation pipeline. This is the most interesting thing about the company
  right now, and also the least proven.

**Verdict framing:** legitimate security vendor with credible research and a marketing
and billing posture that behaves like a growth-stage consumer subscription business.
Those two things are both true simultaneously.

---

## 2. Company profile

| Field | Detail |
| --- | --- |
| Legal entity | Guardio Ltd. |
| Founded | 2018 |
| Headquarters | Tel Aviv, Israel |
| Founders | Amos Peled (CEO), Michael Vainshtein (CTO), Daniel Sirota (VP R&D) |
| Employees | ~127–158 (sources disagree; see note) |
| Users | ~1.5M globally; 700,000+ Chrome Web Store installs |
| Model | B2C subscription (~99% of revenue) |
| ARR | ~$100M reached in 2026 (from ~$12.5M at Series A in 2021) |

### Founders

All three founders served together in an elite Israeli cyber-intelligence unit under the
Prime Minister's Office, and won the Israel National Security Award for that service.
Peled became CEO at 23 and is reported as one of the youngest ever recipients of the award.

Guardio is their **second** company. The first, **Arpeely** (founded 2017), builds
machine-learning media-acquisition algorithms for managed and real-time-bidding ad
environments. This adtech lineage is worth holding in mind when reading both the
company's growth trajectory and the marketing criticism in §7 — performance-marketing
fluency is clearly a core institutional competence, not an accident.

> **Headcount discrepancy:** company-profile aggregators report 127 employees, while
> Growjo lists 158 as of 30 June 2026 (+18% YoY). Both are third-party estimates;
> neither is company-confirmed. Treat ~130–160 as the range.

---

## 3. Funding and financials

| Round | Date | Amount | Lead | Notes |
| --- | --- | --- | --- | --- |
| Bootstrapped | 2018–2021 | — | — | Reached 1M extension users with no outside capital |
| Series A | Dec 2021 | $47M | Tiger Global | Also Vintage Investment Partners, Cerca Partners, Union VC, Samsung Next. Reported at a **$500M valuation**. ~100k paying users, ~$12.5M ARR at the time. |
| Series B | 19 Nov 2025 | $80M | ION Asset Management (ION Crossover Partners) | Returning: Vintage Investment Partners, Union Tech Ventures, Emerge Ventures. |
| **Total** | | **~$127M** | | |

### Growth metrics

- Three consecutive years of **>100% YoY ARR growth** preceding the Series B.
- Guided to surpass **$100M ARR** in early 2026; later reporting indicates the milestone
  was hit during 2026.
- Implied trajectory: ~$12.5M ARR (2021) → ~$100M ARR (2026), roughly 8x over five years.

> **Valuation caution:** the widely-circulated **$500M** figure attaches to the *2021
> Series A*, not the Series B. Several data providers (e.g. PrivCo) still show stale
> records pairing "$47M raised / $500M valuation," which predates the 2025 round
> entirely. A post-Series-B valuation does not appear to have been publicly disclosed.
> Do not cite $500M as current.

---

## 4. Product

### 4.1 What it actually is

A **browser-layer** security product, not a system antivirus. It inspects and blocks
threats at the point of browsing rather than scanning the disk. Understanding this
boundary is essential to evaluating it fairly — most negative comparisons against
Norton/Bitdefender are category errors.

**Platforms:** Chrome and Microsoft Edge extensions (desktop); iOS and Android apps.

### 4.2 Feature set

| Capability | Free | Paid |
| --- | --- | --- |
| Manual security scan (sites, extensions, breaches, hijackers) | ✅ | ✅ |
| Reports risks found | ✅ | ✅ |
| **Fixes / removes** identified threats | ❌ | ✅ |
| Real-time malicious site & phishing blocking | ❌ | ✅ |
| Malicious browser-extension blocking | ❌ | ✅ |
| Download scanning | ❌ | ✅ |
| Browser hijacker protection | ❌ | ✅ |
| Data-breach & email monitoring | ❌ | ✅ |
| Scam SMS / text filtering (mobile) | ❌ | ✅ |
| Phishing email filtering (supported mail services) | ❌ | ✅ |
| Identity-theft insurance | ❌ | ✅ |
| 24/7 support hotline | ❌ | ✅ |

The free tier is best understood as a **diagnostic funnel**: it surfaces problems and
requires payment to act on them.

### 4.3 What it explicitly does not do

- Not a system-wide antivirus — no full-disk scanning, no detection of dormant
  pre-existing infections.
- Not an ad blocker.
- No VPN, no password manager.
- No Firefox or Safari extension (per most recent product coverage).

### 4.4 Pricing (2026)

| Plan | Users | Monthly | Annual | Effective monthly (annual) |
| --- | --- | --- | --- | --- |
| Individual | 1 | $14.99 | $119.88 | $9.99 |
| Duo | 2 | $22.99 | $183.90 | $7.66 |
| Family | 5 | $34.99 | $279.00 | $4.65 |

All paid tiers carry **identical functionality**; higher tiers only extend seat count.
Family works out to under $5/user/month, which is the best value in the lineup by a
wide margin.

### 4.5 Mobile app

The August 2026 Help Net Security showcase describes the mobile app's positioning as
turning **breach alerts into a recovery plan** — checking monitored addresses against
known breaches, then walking the user through remediation, alongside phishing-email
identification, malicious-site blocking, scam-SMS detection, and security notifications,
all configurable in-app.

---

## 5. Guardio Labs — threat research

This is the strongest part of the company's public record, and it is the main reason
Guardio is treated as a serious vendor by the security press rather than as another
consumer-subscription skin.

### 5.1 CVE-2024-21388 — Microsoft Edge silent extension install

- **Flaw:** the private `edgeMarketingPagePrivate` API performed insufficient validation,
  allowing any attacker able to execute JavaScript on a `bing.com` or `microsoft.com`
  page to install arbitrary extensions from the Edge Add-ons store — with broad
  permissions, no user consent, no interaction.
- **Class:** Elevation of Privilege. **CVSS 6.5** (moderate, per MSRC).
- **Timeline:** disclosed to Microsoft November 2023 → patched February 2024.
- Textbook responsible disclosure, and a genuinely nasty bug class.

### 5.2 SubdoMailing (February 2024)

The company's highest-profile campaign discovery.

- **8,000+ domains** and **13,000+ subdomains** of legitimate organizations hijacked,
  across **~22,000 unique IPs**.
- **~5 million malicious emails per day**, running since at least September 2022.
- **Method:** CNAME hijacking (finding subdomain CNAMEs pointing at lapsed external
  domains, then re-registering those domains) plus SPF record exploitation — inheriting
  the victim brand's email-authentication reputation.
- **Victims include:** MSN, VMware, McAfee, eBay, Marvel, CBS, The Economist, NYC.gov,
  PwC, Pearson, Cornell University, UNICEF, ACLU, Symantec, Better Business Bureau.
- **Attribution:** a threat actor Guardio named **ResurrecAds**, monetizing via
  manipulation of the digital advertising ecosystem.

Guardio also shipped a free public subdomain-takeover checker off the back of it.

### 5.3 Salesforce zero-day (2023)

Discovery of a phishing campaign abusing a zero-day in Salesforce's legitimate email
services. Salesforce publicly credited Guardio Labs for responsible disclosure.

### 5.4 VibeScamming Benchmark v1.0 (April 2025)

Benchmarked how readily major AI systems could be walked into building working phishing
infrastructure, scored 1–10 (higher = more resistant):

| Platform | Score | Behavior |
| --- | --- | --- |
| ChatGPT | 8.0 | Strongest resistance |
| Claude | 4.3 | Solid initial pushback, but persuadable via "security research" framing |
| Lovable | 1.8 | Generated a full scam page **plus** an admin dashboard exposing captured credentials, IPs, timestamps, and plaintext passwords |

Notably, Lovable — the worst performer — later became Guardio's first B2B customer
(§6.1). Read charitably, the research created the demand; read cynically, the vendor
publicly scored a prospect's product 1.8/10 and then sold them the fix.

### 5.5 Scamlexity (August 2025) — agentic browser security

The research that put Guardio in mainstream tech press. Testing Perplexity's **Comet**
AI browser:

- **Fake storefront:** asked to buy an Apple Watch on a counterfeit Walmart site, Comet
  proceeded to checkout, auto-filled card details and address, and **completed the
  purchase** without seeking confirmation.
- **Phishing email:** a spoofed Wells Fargo email with a live phishing URL — Comet opened
  it without objection and **entered banking credentials** into the phishing page.
- **PromptFix prompt injection:** hidden instructions embedded in a fake CAPTCHA page
  were read by the agent as legitimate commands, triggering a malicious download.

Guardio coined **"Scamlexity"** for the resulting condition: *"a complex new era of scams,
where AI convenience collides with a new, invisible scam surface and humans become the
collateral damage."* The critical insight is structural — with an agent transacting on
the user's behalf, the human is removed from exactly the moment where scam recognition
historically happened.

Follow-on work includes **AgenticBlabbering** (AI browsers' verbose reasoning traces as
an exploitable scam surface) and March 2026 findings that Comet could be walked into a
phishing scam in **under four minutes**.

### 5.6 Ongoing tracking

- **Malvertising** impersonating Zoom, Adobe, Canva, and Slack download pages, delivering
  credential stealers.
- **AI-generated phishing** — grammatically perfect, pixel-accurate brand recreation,
  passing standard authentication checks.
- **Quishing** (malicious QR codes), which drew an FBI public warning.

---

## 6. Strategy and market position

### 6.1 The AI pivot

Guardio has repositioned from "browser extension" to **"Safe Browsing for AI"** —
protection for AI browsers, autonomous agents, and generative platforms. The Series B
narrative was explicitly about bringing enterprise-grade detection to consumers "in the
age of AI."

The **Lovable partnership** (October 2025, one month before the raise) is the proof
point: Guardio's detection engine is embedded directly into Lovable's GenAI chain, so
every site generated on the platform is scanned for phishing, scams, and impersonation
**before it goes live**. The distinction Guardio draws is that legacy Safe Browsing
relies on domain reputation and therefore structurally cannot catch a site that did not
exist a minute ago; detecting *at creation* sidesteps the cold-start problem.

The motivation was concrete: in August 2025 researchers found tens of thousands of
Lovable-built URLs being used in phishing, crypto scams, and malware delivery.

This appears to be one of the first large-scale integrations between an AI web-development
platform and a security vendor aimed at AI-generated abuse at the point of creation.

### 6.2 Business mix

~99% of revenue is consumer subscription. Management has framed the Lovable deal as
evidence that the same detection engine addresses a B2B market where few alternatives
exist. Stated channel expansion: **MSPs, value-added resellers, and browser ecosystems**.

### 6.3 Competitive landscape

| Segment | Players | Guardio's position |
| --- | --- | --- |
| Free browser guards | Malwarebytes Browser Guard, Bitdefender TrafficLight | Undercut on price ($0); Guardio must justify $120+/yr against them |
| Full-suite consumer security | Norton 360 + LifeLock, Bitdefender, Avast, ESET, TotalAV | Broader (VPN, password manager, system AV, credit monitoring) and often cheaper |
| Identity-first | Aura, LifeLock | Deeper identity/credit monitoring; Guardio is browser-first |
| **AI/agentic browsing security** | Sparse | **Guardio's genuine differentiation** |

The honest read: in the *legacy* consumer-security market Guardio is an expensive
partial solution competing against cheaper, broader suites and free alternatives.
Its defensible position is the AI/agentic threat surface, where it has first-mover
research credibility and few direct competitors. The Series B is essentially a bet on
that second market becoming the main one.

---

## 7. Criticism, risks, and red flags

This section is deliberately unsparing, because the positive material above is easy to
find and this is not.

### 7.1 No independent lab certification ⚠️ *most material*

Guardio has **never been submitted to AV-TEST, AV-Comparatives, or SE Labs**. The
company's explanation — that these labs test full-system malware detection, which is not
what Guardio does — is partially fair, but AV-Comparatives does run anti-phishing and
browser-security certifications that would be applicable.

The consequence: every efficacy number in circulation is either vendor-supplied or from
a commercial review site. Third-party hands-on testing reports **131/150 simulated
threats caught** and, in one test, **100% phishing detection**. These are encouraging but
not equivalent to lab certification, and several publishing reviewers operate affiliate
relationships with the vendor.

### 7.2 Billing and cancellation

The most consistent complaint cluster across BBB, PissedConsumer, and Trustpilot's
lower tail:

- **Annual plans advertised at monthly-equivalent prices** — users expecting ~$9.99/mo
  are charged $119.88 upfront.
- **Free trial places an authorization hold for the full subscription amount**, which
  many users read as an actual charge.
- **Cancellation reported as difficult**, with continued billing after cancellation
  confirmations in some accounts.
- **Support is primarily email**, and slow — despite paid tiers advertising a 24/7
  hotline. That inconsistency between the marketed and reported support experience is
  itself a flag.

### 7.3 Fear-based marketing

Recurring criticism that Guardio:

- Flags long-resolved historical breaches and benign sites as "high risk."
- Uses countdown timers and urgency mechanics in its purchase funnel.
- Saturates YouTube sponsorship and display advertising to a degree that makes the brand
  read as marketing-led rather than security-led.

Given the founders' Arpeely adtech background (§2), performance-marketing intensity is
predictable. It does not make the product ineffective — but it does mean in-product risk
scoring should be read as partly a conversion instrument.

### 7.4 Privacy

Guardio collects anonymized browsing behavior **including sites and URLs visited**, plus
country, IP address, install time, name, email, and the last four digits and type of
payment card.

Its stated position: collection is limited to what the service requires, the business
model is subscriptions rather than data sales, and it is GDPR compliant.

That claim is credible on its face. The tension worth naming: a browser extension with
full URL visibility, operated by founders who concurrently run a real-time-bidding adtech
company, is a structure that requires trust in policy rather than in architecture. There
is no public evidence of misuse — this is a governance observation, not an allegation.

### 7.5 Ratings — the two-sided picture

- **Trustpilot: 4.4/5**, with 86% of reviews at 4–5 stars.
- **Chrome Web Store: 4.5/5** across 1,300+ reviews, 700,000+ installs.
- **BBB and PissedConsumer:** hundreds of complaints, concentrated almost entirely in
  billing, cancellation, and support responsiveness rather than in protection failures.

The signal in that split is clear and consistent: **users are largely satisfied with the
product and frequently dissatisfied with the commercial relationship.**

### 7.6 Business risks

- **Platform dependency.** The core product is a browser extension. Chrome's Manifest V3
  transition, extension-permission tightening, or native browser security improvements
  are existential-adjacent risks Guardio does not control.
- **Big-tech encroachment.** Google Safe Browsing, Microsoft Defender SmartScreen, and
  built-in browser protections improve continuously and are free.
- **Growth-multiple exposure.** Three years of >100% ARR growth is priced into the
  Series B. Consumer-subscription businesses with high-friction cancellation flows can
  carry churn and refund liabilities that ARR headlines obscure.
- **Regulatory exposure.** Negative-option billing and cancellation-friction practices
  are an active enforcement priority for consumer-protection regulators. Guardio's
  complaint profile sits squarely in that category. No enforcement action against
  Guardio was found in this research — but the practice pattern is the kind regulators
  currently target.

---

## 8. Assessment

**Where Guardio is genuinely strong**

1. Threat research that stands on its own merits — a real CVE, a major campaign
   discovery, and the most-cited early work on agentic browser security.
2. First-mover credibility in AI/agentic threat protection, converted into an actual
   commercial integration rather than just blog posts.
3. Capital efficiency — bootstrapped to 1M users before raising a dollar.
4. Sustained triple-digit growth to ~$100M ARR.

**Where it is weak**

1. No independent lab certification, which for a security product is the gap that matters
   most.
2. A commercial funnel — pricing presentation, trial holds, cancellation friction — that
   generates complaint volume disproportionate to product dissatisfaction.
3. Narrow protection scope at a price point competing against broader suites.
4. Marketing that leans on fear in ways that compromise the credibility of its own
   in-product risk signals.

**Bottom line**

Guardio is a legitimate, well-funded, technically credible security company whose
research arm punches well above its weight, wrapped in a consumer-subscription go-to-market
that behaves the way aggressive consumer-subscription businesses generally behave. The
security is not the questionable part; the sales motion is.

For a prospective **user**: worthwhile if you want browser and phishing protection
specifically and you buy the annual Family plan; poor value at the monthly Individual
price, where broader suites cost less. Buy through a payment method you can control.

For a prospective **investor or partner**: the AI/agentic security thesis is the real
asset and is credibly differentiated. Diligence should concentrate on churn, refund
rates, and CAC — because the complaint profile suggests the growth number and the
retention number may tell different stories.

---

## 9. Research notes and limitations

- `guard.io` and several news domains (SiliconANGLE, Calcalist, FinSMEs, The Hacker News,
  KrebsOnSecurity) were **blocked by this environment's network egress proxy**. Findings
  are therefore assembled from search-index summaries and accessible secondary coverage
  rather than from primary pages in every case. Figures sourced only to a single
  aggregator are flagged inline.
- Several consumer "review" sites covering Guardio operate **affiliate relationships**
  with security vendors, including with Guardio and with its direct competitors
  (Aura, Norton/LifeLock, Incogni, DeleteMe all publish Guardio assessments while
  selling competing products). Efficacy and "is it legit" claims from those sources are
  treated as directional only.
- Post-Series-B valuation, current churn, refund rates, and CAC are **not public**.
- Employee count and the exact date of the $100M ARR milestone are third-party estimates.

---

## 10. Sources

**Company and funding**
- [Crunchbase — Guardio](https://www.crunchbase.com/organization/guardio)
- [PitchBook — Guardio company profile](https://pitchbook.com/profiles/company/439816-60)
- [Tracxn — Guardio funding rounds and investors](https://tracxn.com/d/companies/guardio/__YimVczYmVBwYc94sAsPvxuKjXac7D_wX5lvBlHOD-9w/funding-and-investors)
- [TechCrunch — Guardio raises $47M led by Tiger Global (Dec 2021)](https://techcrunch.com/2021/12/14/cybersecurity-startup-guardio-now-with-1m-users-of-its-browser-extension-raises-its-first-funding-47m-led-by-tiger-global/)
- [FinSMEs — Guardio raises $80M Series B](https://www.finsmes.com/2025/11/guardio-raises-80m-in-series-b-funding.html)
- [SiliconANGLE — Guardio lands $80M for AI-driven protection](https://siliconangle.com/2025/11/19/guardio-lands-80m-expand-ai-driven-protection-everyday-internet-users/)
- [Calcalist / CTech — Guardio raises $80 million](https://www.calcalistech.com/ctechnews/article/h1rgcvixzg)
- [Calcalist / CTech — Guardio $47M Series A](https://www.calcalistech.com/ctech/articles/0,7340,L-3925009,00.html)
- [FinTech Global — Guardio bags $80m](https://fintech.global/2025/11/25/guardio-bags-80m-as-demand-for-consumer-cyber-soars/)
- [Globes — Israeli cybersecurity co Guardio raises $80m](https://www.globes.co.il/news/article.aspx?did=1001526976)
- [Sacra — Guardio funding, news & analysis](https://sacra.com/c/guardio/)
- [Growjo — Guardio revenue and headcount](https://growjo.com/company/Guardio)
- [Craft.co — Guardio executive team](https://craft.co/guardio/executives)

**Guardio Labs research**
- [Guardio Labs — Scamlexity: agentic AI browsers tested](https://guard.io/labs/scamlexity-we-put-agentic-ai-browsers-to-the-test-they-clicked-they-paid-they-failed)
- [Guardio Labs — VibeScamming benchmark](https://guard.io/labs/vibescamming-from-prompt-to-phish-benchmarking-popular-ai-agents-resistance-to-the-dark-side)
- [Guardio Labs — SubdoMailing](https://guard.io/labs/subdomailing-thousands-of-hijacked-major-brand-subdomains-found-bombarding-users-with-millions)
- [Guardio Labs — CVE-2024-21388, Edge marketing API](https://guard.io/labs/cve-2024-21388-microsoft-edges-marketing-api-exploited-for-covert-extension-installation)
- [The Hacker News — 8,000+ subdomains of trusted brands hijacked](https://thehackernews.com/2024/02/8000-subdomains-of-trusted-brands.html)
- [The Hacker News — Microsoft Edge bug allowed silent extension installs](https://thehackernews.com/2024/03/microsoft-edge-bug-could-have-allowed.html)
- [The Hacker News — Lovable most vulnerable to VibeScamming](https://thehackernews.com/2025/04/lovable-ai-found-most-vulnerable-to.html)
- [The Hacker News — Comet AI browser tricked in under four minutes](https://thehackernews.com/2026/03/researchers-trick-perplexitys-comet-ai.html)
- [The Hacker News — PromptFix exploit in AI browsers](https://thehackernews.com/2025/08/experts-find-ai-browsers-can-be-tricked.html)
- [BleepingComputer — Hijacked subdomains used in massive spam campaign](https://www.bleepingcomputer.com/news/security/hijacked-subdomains-of-major-brands-used-in-massive-spam-campaign/)
- [Kaspersky — SubdoMailing domain hijacking analysis](https://www.kaspersky.co.uk/blog/domain-hijacking-subdomailing/27537/)
- [Dark Reading — Guardio uncovers Salesforce email services zero-day](https://www.darkreading.com/cyberattacks-data-breaches/guardio-uncovers-zero-day-vulnerability-in-salesforce-s-email-services)
- [KrebsOnSecurity — Guardio Labs tag](https://krebsonsecurity.com/tag/guardio-labs/)
- [Engadget — AI browsers may be the best thing that ever happened to scammers](https://www.engadget.com/ai/ai-browsers-may-be-the-best-thing-that-ever-happened-to-scammers-220315936.html)
- [CyberInsider — AI browsers fooled by phishing and fake stores](https://cyberinsider.com/ai-browsers-fooled-by-phishing-and-fake-stores-in-real-world-tests/)
- [Wiz — Agentic browser security 2025 year-end review](https://www.wiz.io/blog/agentic-browser-security-2025-year-end-review)
- [PR Newswire — Guardio Labs uncovers thousands of compromised domains](https://www.prnewswire.com/il/news-releases/guardio-labs-uncovers-thousands-of-compromised-domains-used-to-send-mass-malicious-emails-302071035.html)

**Product, pricing, and reviews**
- [Help Net Security — Guardio Mobile Security product showcase (Aug 2026)](https://www.helpnetsecurity.com/2026/08/03/product-showcase-guardio-mobile-security/)
- [Cybernews — Guardio review: features, pricing, test results](https://cybernews.com/best-antivirus-software/guardio-review/)
- [Security.org — Guardio review and pricing](https://www.security.org/antivirus/guardio/)
- [All About Cookies — Guardio review](https://allaboutcookies.org/guardio-review)
- [SafetyDetectives — Guardio review](https://www.safetydetectives.com/best-antivirus/guardio/)
- [HostAdvice — Guardio review: browser security tested](https://hostadvice.com/antivirus-software/guardio-review/)
- [Capterra — Guardio pricing and alternatives](https://www.capterra.com/p/205343/Guardio/)
- [Guardio — Plans and pricing](https://guard.io/plans)
- [Chrome Web Store — Guardio Protection for Chrome](https://chromewebstore.google.com/detail/guardio-protection-for-ch/gjfpmkejnolcfklaaddjnckanhhgegla)
- [Guardio — Privacy policy](https://guard.io/privacy-policy)

**Criticism and consumer complaints**
- [Better Business Bureau — Guardio Ltd. complaints](https://www.bbb.org/us/ny/monsey/profile/computer-software/guardio-0121-87150897/complaints/)
- [Trustpilot — Guardio reviews](https://www.trustpilot.com/review/guard.io)
- [PissedConsumer — Guardio reviews and complaints](https://guardio.pissedconsumer.com/review.html)
- [Incognitown — Is Guardio legit? A no-nonsense look at its practices](https://incognitown.com/privacy-2/is-guardio-legit)
- [OneRep — Is Guardio legit?](https://onerep.com/blog/is-guardio-legit)
- [Incogni — Guardio review: what it gets right, what it doesn't](https://blog.incogni.com/guardio-review/)

**Partnerships and market**
- [Guardio — Lovable integration announcement](https://guard.io/blog/lovable-integrates-guardio-to-ensure-safe-responsible-ai-development)
- [Calcalist / CTech — Lovable and Guardio partner on AI-generated web abuse](https://www.calcalistech.com/ctechnews/article/hjniwdwjbe)
- [Digital Trends — Lovable adds safe browsing engine](https://www.digitaltrends.com/computing/lovable-adds-safe-browsing-engine-to-protect-you-from-scammy-vibecoded-websites/)
- [Cybernews — Guardio vs Aura](https://cybernews.com/identity-theft-protection/guardio-vs-aura/)
- [Cybernews — Guardio vs Norton](https://cybernews.com/identity-theft-protection/guardio-vs-norton/)
- [CB Insights — Guardio vs Malwarebytes](https://www.cbinsights.com/compare/guardio-vs-malwarebytes)
