"""Build docs/SmartHR_Email_Setup.pdf, the email setup guide (careers website + SmartHR). Edit the HTML below, then run:
.venv/Scripts/python scripts/build_mailbox_guide.py
"""
from datetime import date
from pathlib import Path

import pymupdf

OUT = Path(__file__).resolve().parent.parent / "docs" / "SmartHR_Email_Setup.pdf"
MAILBOX = "noreply@kiaratech.ai"

CSS = """
* { font-family: sans-serif; }
body { font-size: 9.5pt; line-height: 1.45; color: #1c2a52; }
h1 { font-size: 20pt; margin: 0 0 4pt 0; color: #1c2a52; }
h2 { font-size: 13pt; margin: 16pt 0 6pt 0; color: #1f6f6b; border-bottom: 1px solid #d6dee8; padding-bottom: 3pt; }
h3 { font-size: 10.5pt; margin: 12pt 0 4pt 0; color: #1c2a52; }
p { margin: 0 0 6pt 0; }
.lead { font-size: 10.5pt; color: #46557a; margin-bottom: 10pt; }
.sub { color: #5f6b88; font-size: 9pt; margin-bottom: 14pt; }
ul, ol { margin: 0 0 6pt 0; padding-left: 14pt; }
li { margin-bottom: 3pt; }
table { width: 100%; border-collapse: collapse; margin: 4pt 0 8pt 0; }
th { text-align: left; padding: 4pt 5pt; font-size: 9pt; color: #1f6f6b; border: 1px solid #b9dcd8; }
td { padding: 4pt 5pt; vertical-align: top; font-size: 9pt; border: 1px solid #d6dee8; }
.mono { font-family: monospace; font-size: 8.5pt; color: #175753; }
pre { font-family: monospace; font-size: 8pt; border: 1px solid #d6dee8; padding: 6pt; margin: 4pt 0 8pt 0;
      line-height: 1.35; color: #1c2a52; }
.ok { border: 1px solid #1f6f6b; padding: 6pt 8pt; margin: 6pt 0 8pt 0; color: #175753; }
.box { border: 1px solid #d9a441; padding: 6pt 8pt; margin: 6pt 0 8pt 0; color: #5a3b00; }
"""

HTML = f"""
<h1>SmartHR email setup</h1>
<p class="lead">What has to be set up in Microsoft 365 so that applications from the careers page reach SmartHR, and
SmartHR can send shortlisted emails and schedule first-round Teams interviews, all through <b>{MAILBOX}</b>.</p>
<p class="sub">Version {date.today():%d %B %Y} &#183; follows the SmartHR System Design, "Secure access to the mailbox"</p>

<h2>1. How it works</h2>
<table>
<tr><th width="5%">#</th><th width="25%">Who</th><th>What happens</th></tr>
<tr><td>1</td><td>Applicant</td><td>Fills in the job application form on the Kiaratech website and attaches a CV.</td></tr>
<tr><td>2</td><td>Website</td><td>Sends the application as an email <b>from {MAILBOX} to {MAILBOX}</b>: the form
answers in the body, the CV attached, the applicant's address as Reply-To, the job code in the subject.</td></tr>
<tr><td>3</td><td>SmartHR</td><td>Reads the inbox every 2 minutes. It takes the candidate's name and email from the
form (never from the sender, which is always {MAILBOX}), scores the CV against that job's checklist, and moves the
email to <i>SmartHR Processed</i>.</td></tr>
<tr><td>4</td><td>HR</td><td>Reviews the ranked shortlist in SmartHR and clicks Send on each email or invite.</td></tr>
<tr><td>5</td><td>SmartHR</td><td>Sends the shortlisted email from {MAILBOX}. It also books the first-round
<b>Teams interview</b> from {MAILBOX}, choosing from the interviewer's free time, and invites the candidate and the
interviewer.</td></tr>
</table>

<h2>2. Is this right: two apps, each with only what it needs?</h2>
<div class="ok"><b>Yes.</b> The website and SmartHR are <b>two separate apps</b>, both limited to {MAILBOX} only.
If the website is ever compromised, it can send email as {MAILBOX} but cannot read any CV or calendar.</div>
<table>
<tr><th width="18%">App</th><th width="38%">Access on {MAILBOX} only</th><th>Why</th></tr>
<tr><td><b>Website</b><br/>(careers form)</td><td><span class="mono">Application Mail.Send</span></td>
<td>Send the application email. Nothing else: it never reads the mailbox.</td></tr>
<tr><td rowspan="3"><b>SmartHR</b></td><td><span class="mono">Application Mail.ReadWrite</span></td>
<td>Read the inbox, download the CVs, move handled emails to the SmartHR folders, create the draft of each
shortlisted email.</td></tr>
<tr><td><span class="mono">Application Mail.Send</span></td><td>Send that draft (shortlisted email).</td></tr>
<tr><td><span class="mono">Application Calendars.ReadWrite</span></td><td>Create the Teams interview, which sends
the invites. Read the interviewer's free/busy to offer free slots; their calendar itself is not opened.</td></tr>
</table>
<div class="box"><b>Needs an admin: yes.</b> Access to a mailbox without a signed-in person needs <i>application</i>
permissions, which only admins can grant. Grant them in <b>Exchange</b> (step 4), not under API permissions in Entra.
Granting them in Entra would give access to <b>every mailbox in the company</b>.</div>

<h2>3. Who does what</h2>
<table>
<tr><th width="24%">Who</th><th width="34%">Microsoft 365 role</th><th>Steps</th></tr>
<tr><td>M365 admin</td><td>User Administrator + License Administrator</td><td>1: create the mailbox</td></tr>
<tr><td>Website team and SmartHR server owner</td><td>none</td><td>2: one certificate for each app</td></tr>
<tr><td>Entra admin</td><td>Application Administrator</td><td>3: register the two apps</td></tr>
<tr><td>Exchange admin</td><td>Exchange Administrator</td><td>4: allow each app on {MAILBOX} only</td></tr>
<tr><td>Website team</td><td>none</td><td>5: send the application email in the agreed format</td></tr>
<tr><td>SmartHR server owner</td><td>none</td><td>6 and 7: configure SmartHR and test it</td></tr>
</table>
<p>No Global Administrator is needed.</p>

<h2>4. Steps</h2>

<h3>Step 1 &#183; Create the mailbox (M365 admin)</h3>
<ol>
<li>Microsoft 365 admin centre &#8594; Users &#8594; Add a user: <b>{MAILBOX}</b>, display name e.g.
<i>Kiaratech Careers</i>.</li>
<li>Assign a licence with <b>Exchange Online and Teams</b> (e.g. Microsoft 365 Business Basic). It must be a licensed
user mailbox: a shared mailbox cannot organise Teams meetings.</li>
<li>Block sign-in for this user. Nobody logs in to it; both apps use their own certificates.</li>
</ol>

<h3>Step 2 &#183; One certificate per app</h3>
<p>Each app signs in with its own certificate, never a password. On the SmartHR server:</p>
<pre>.venv/Scripts/python scripts/make_cert.py smarthr
.venv/Scripts/python scripts/make_cert.py website   # or the website team makes its own</pre>
<ul>
<li><span class="mono">secrets/&lt;name&gt;-key.pem</span> is the <b>private key</b>. The SmartHR key stays on the SmartHR
server and the website key goes only to the website server. Never email or commit either. In production, keep them in
Azure Key Vault.</li>
<li><span class="mono">secrets/&lt;name&gt;.cer</span> is the <b>public certificate</b>: give it to the Entra
admin. The script also prints the <b>thumbprint</b>.</li>
<li>Certificates are valid for 2 years: set renewal reminders.</li>
</ul>

<h3>Step 3 &#183; Register the two apps (Entra admin)</h3>
<p>Do this twice, once for <b>Kiaratech Careers Website</b> (with <span class="mono">website.cer</span>) and once for
<b>SmartHR</b> (with <span class="mono">smarthr.cer</span>):</p>
<ol>
<li>Entra admin centre &#8594; App registrations &#8594; New registration. "Accounts in this organisational directory
only", no redirect URI.</li>
<li>Certificates &amp; secrets &#8594; Certificates &#8594; Upload certificate.</li>
<li>Note the <b>Application (client) ID</b> and the <b>Directory (tenant) ID</b> (Overview). Also note the
<b>Object ID of the enterprise application</b> (Enterprise applications &#8594; the app &#8594; Overview), not the app
registration's object ID.</li>
<li><b>Do not</b> add Microsoft Graph application permissions. Step 4 grants them, limited to {MAILBOX}.</li>
</ol>

<h3>Step 4 &#183; Allow each app on {MAILBOX} only (Exchange admin)</h3>
<p>In Exchange Online PowerShell:</p>
<pre>Connect-ExchangeOnline

# One scope: only the careers mailbox
New-ManagementScope -Name "Careers mailbox only" `
  -RecipientRestrictionFilter "PrimarySmtpAddress -eq '{MAILBOX}'"

# Make both apps known to Exchange (ENTERPRISE APPLICATION object ids from step 3)
New-ServicePrincipal -AppId &lt;website-client-id&gt; `
  -ObjectId &lt;website-enterprise-app-object-id&gt; -DisplayName "Careers website"
New-ServicePrincipal -AppId &lt;smarthr-client-id&gt; `
  -ObjectId &lt;smarthr-enterprise-app-object-id&gt; -DisplayName "SmartHR"

# Website: send only
New-ManagementRoleAssignment -App &lt;website-client-id&gt; -Role "Application Mail.Send" `
  -CustomResourceScope "Careers mailbox only"

# SmartHR: read/write mail, send, calendar
$roles = "Application Mail.ReadWrite", "Application Mail.Send", "Application Calendars.ReadWrite"
$roles | ForEach-Object {{
    New-ManagementRoleAssignment -App &lt;smarthr-client-id&gt; -Role $_ `
      -CustomResourceScope "Careers mailbox only"
}}

# Check: InScope must be True for {MAILBOX} and False for anyone else
Test-ServicePrincipalAuthorization -Identity &lt;smarthr-client-id&gt; -Resource {MAILBOX}
Test-ServicePrincipalAuthorization -Identity &lt;smarthr-client-id&gt; `
  -Resource someone.else@kiaratech.ai
Test-ServicePrincipalAuthorization -Identity &lt;website-client-id&gt; -Resource {MAILBOX}</pre>
<p>Changes can take up to an hour to apply.</p>

<h3>Step 5 &#183; The application email (website team)</h3>
<p>The website sends one email per application with Microsoft Graph
<span class="mono">POST /users/{MAILBOX}/sendMail</span>, signed in as the website app:</p>
<table>
<tr><th width="22%">Part</th><th>Content</th></tr>
<tr><td>From</td><td>{MAILBOX}</td></tr>
<tr><td>To</td><td>{MAILBOX}</td></tr>
<tr><td>Reply-To</td><td>The applicant's email address. SmartHR uses this first.</td></tr>
<tr><td>Subject</td><td><span class="mono">[JOB-CODE] Application - Job title - First Last</span>. The job code in
square brackets is required: it must match the job's code in SmartHR exactly (e.g. <span class="mono">[JOB-1023]</span>).
</td></tr>
<tr><td>Body</td><td>Plain text, one form answer per line as <span class="mono">Label: value</span>. Each job's form
may ask different questions: SmartHR keeps whatever fields arrive and shows them to HR. Always include
<span class="mono">First name</span>, <span class="mono">Last name</span> and <span class="mono">Email</span>.</td></tr>
<tr><td>Attachment</td><td>The CV as a normal file attachment: PDF, DOCX or TXT. <b>Up to 3 MB</b> (see below).</td></tr>
<tr><td>Sent Items</td><td>Use <span class="mono">saveToSentItems: false</span>, so Sent Items is not filled with a
copy of every application.</td></tr>
</table>
<p>Example body:</p>
<pre>First name: Asha
Last name: Kumar
Email: asha.kumar@example.com
Phone: +91 98000 00000
Current location: Kochi
LinkedIn profile: https://www.linkedin.com/in/example
Expected CTC: 15 LPA
Notice period (days): 30
Anything else we should know?: Available after 5 pm.</pre>
<div class="box"><b>CV size: change the form to "up to 3 MB".</b> With only Mail.Send, Microsoft Graph accepts
attachments up to about 3 MB in one send. Larger files need an upload session, which also needs Mail.ReadWrite: the
website would then be able to read every CV in the mailbox. A 3 MB limit is enough for nearly all CVs.</div>
<div class="box"><b>Website security.</b> The form is open to the internet: add a CAPTCHA and rate limiting, accept only
PDF/DOCX/TXT, and check the size before sending. SmartHR treats everything in the email as data. The AI reads only the
CV, never the form text.</div>

<h3>Step 6 &#183; Configure SmartHR (<span class="mono">.env</span> on the SmartHR server)</h3>
<pre>SMARTHR_MAILBOX={MAILBOX}
SMARTHR_TENANT_ID=&lt;Directory (tenant) ID&gt;
SMARTHR_CLIENT_ID=&lt;SmartHR Application (client) ID&gt;
SMARTHR_CERT_PATH=secrets/smarthr-key.pem
SMARTHR_CERT_THUMBPRINT=&lt;printed by make_cert.py smarthr&gt;
# test mode: keep it set until testing is done
SMARTHR_EMAIL_REDIRECT=&lt;your own address&gt;</pre>
<p>In SmartHR, give each job the <b>same code</b> as the website uses, and approve its checklist. Then restart the
API.</p>

<h3>Step 7 &#183; Test it (in test mode)</h3>
<div class="box">Never test with real applicants' addresses. Keep <span class="mono">SMARTHR_EMAIL_REDIRECT</span>
set: every shortlisted email and invite then goes to you, with <span class="mono">[TEST for &#8230;]</span> in the
subject.</div>
<ol>
<li><span class="mono">GET /api/v1/mailbox</span> returns <span class="mono">"connected": true</span>.</li>
<li>Submit the website form yourself, with <b>your own email</b> and a test CV.</li>
<li><span class="mono">POST /api/v1/mailbox:check</span> (or wait 2 minutes). The candidate appears on that job's
shortlist with the form details, and the email moves to <i>SmartHR Processed</i>.</li>
<li>Send the shortlisted email: it reaches you.</li>
<li>Enter your own address as interviewer, then Find interview times and Create Teams meeting. Check the invite has a
Teams join link.</li>
<li>Only then remove <span class="mono">SMARTHR_EMAIL_REDIRECT</span> and restart.</li>
</ol>

<h2>5. Later: HR sign-in to SmartHR</h2>
<p>HR will sign in to the SmartHR portal with their own Microsoft account (single sign-on). This is <b>separate</b> from
the mailbox setup above, and it will be set up once the HR account exists:</p>
<ul>
<li>A third app registration for the SmartHR portal, with sign-in only (<span class="mono">openid</span>,
<span class="mono">profile</span>, <span class="mono">User.Read</span>, delegated).</li>
<li>No mailbox or calendar permissions: HR's own mailbox is not used. All email goes through {MAILBOX}.</li>
<li>App roles from the design (Recruiter, HR Manager, Admin), assigned to people by an Entra admin.</li>
</ul>
<p>Until then, the SmartHR portal has no sign-in. Run it only on the internal network.</p>

<h2>6. Troubleshooting</h2>
<table>
<tr><th width="38%">What you see</th><th>Cause</th></tr>
<tr><td>The SmartHR mailbox is not set up</td><td>One of the step 6 values in <span class="mono">.env</span> is
missing.</td></tr>
<tr><td>Could not sign in to Microsoft 365</td><td>Wrong tenant or client ID, a thumbprint that does not match the
uploaded certificate, or an expired certificate.</td></tr>
<tr><td>Access is denied</td><td>Step 4 is missing or not applied yet (up to an hour). Check with
<span class="mono">Test-ServicePrincipalAuthorization</span>.</td></tr>
<tr><td>The website gets "request too large"</td><td>The CV is over about 3 MB (step 5).</td></tr>
<tr><td>Email in <i>SmartHR Needs attention</i></td><td>No <span class="mono">[JOB-CODE]</span> in the subject, a code
that does not exist in SmartHR, a checklist not yet approved, or no PDF/DOCX/TXT attached. Candidate replies to a
shortlisted email also land here. Fix it, then drag the email back to the inbox.</td></tr>
<tr><td>Candidate has no email in SmartHR</td><td>The form sent no Reply-To and no <span class="mono">Email:</span> line.
HR can type it in before sending.</td></tr>
<tr><td>Cannot read the interviewer's calendar</td><td>The organisation hides free/busy, or the interviewer is outside
the organisation.</td></tr>
<tr><td>No Teams link in the invite</td><td>The mailbox has no Teams licence (step 1).</td></tr>
</table>
"""

PAGE = pymupdf.paper_rect("a4")
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 50, 58, 54
BODY = pymupdf.Rect(MARGIN_X, MARGIN_TOP, PAGE.width - MARGIN_X, PAGE.height - MARGIN_BOTTOM)


def build() -> None:
    story = pymupdf.Story(html=HTML, user_css=CSS)
    writer = pymupdf.DocumentWriter(str(OUT))
    more = True
    while more:
        device = writer.begin_page(PAGE)
        more, _ = story.place(BODY)
        story.draw(device)
        writer.end_page()
    writer.close()

    doc = pymupdf.open(OUT)  # header and footer on every page
    for number, page in enumerate(doc, 1):
        page.insert_text((MARGIN_X, 34), "Kiaratech  ·  SmartHR", fontsize=8, fontname="helv", color=(0.12, 0.44, 0.42))
        page.insert_text((PAGE.width - MARGIN_X - 140, 34), "Confidential – internal use", fontsize=8, fontname="helv",
                         color=(0.37, 0.42, 0.53))
        page.draw_line((MARGIN_X, 40), (PAGE.width - MARGIN_X, 40), color=(0.84, 0.87, 0.91), width=0.6)
        page.insert_text((MARGIN_X, PAGE.height - 28), "SmartHR email setup", fontsize=8, fontname="helv",
                         color=(0.37, 0.42, 0.53))
        page.insert_text((PAGE.width - MARGIN_X - 52, PAGE.height - 28), f"Page {number} of {doc.page_count}",
                         fontsize=8, fontname="helv", color=(0.37, 0.42, 0.53))
    doc.set_metadata({"title": "SmartHR email setup", "author": "Kiaratech", "subject": f"Admin setup for {MAILBOX}"})
    doc.saveIncr()
    print(f"{OUT} ({doc.page_count} pages)")


if __name__ == "__main__":
    build()
