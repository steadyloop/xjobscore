# Greenhouse
**Pattern**: `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/content=true`
* absolute_url is the hosted posting + application page.
* Detail with questions: `/v1/boards/{token}/jobs/{job_id}?questions=true`
* Board token is in the careers URL `(boards.greenhouse.io/{token}` or `job-boards.greenhouse.io/{token})`
<details><summary>Sample Outputs</summary>

content=false
---
```json
{
  "absolute_url": "https://stripe.com/jobs/search?gh_jid=7954688",
  "data_compliance": [
    {
      "type": "gdpr",
      "requires_consent": false,
      "requires_processing_consent": false,
      "requires_retention_consent": false,
      "retention_period": null,
      "demographic_data_consent_applies": false
    }
  ],
  "internal_job_id": 3453698,
  "location": {
    "name": "San Francisco, CA"
  },
  "metadata": null,
  "id": 7954688,
  "updated_at": "2026-07-27T11:17:30-04:00",
  "requisition_id": "See Opening ID",
  "title": "Account Executive, AI Sales (Grower)",
  "company_name": "Stripe",
  "first_published": "2026-06-02T08:58:57-04:00",
  "language": "en",
  "application_deadline": null
}

```
content=true
---
```json
{
  "absolute_url": "https://stripe.com/jobs/search?gh_jid=7954688",
  "data_compliance": [
    {
      "type": "gdpr",
      "requires_consent": false,
      "requires_processing_consent": false,
      "requires_retention_consent": false,
      "retention_period": null,
      "demographic_data_consent_applies": false
    }
  ],
  "internal_job_id": 3453698,
  "location": {
    "name": "San Francisco, CA"
  },
  "metadata": null,
  "id": 7954688,
  "updated_at": "2026-07-27T11:17:30-04:00",
  "requisition_id": "See Opening ID",
  "title": "Account Executive, AI Sales (Grower)",
  "company_name": "Stripe",
  "first_published": "2026-06-02T08:58:57-04:00",
  "language": "en",
  "application_deadline": null,
  "content": "&lt;h2&gt;Who we are&lt;/h2&gt;\n&lt;h3&gt;About Stripe&lt;/h3&gt;\n&lt;p&gt;Stripe is a financial infrastructure platform for businesses. Millions of companies - from the world’s largest enterprises to the most ambitious startups - use Stripe to accept payments, grow their revenue, and accelerate new business opportunities. Our mission is to increase the GDP of the internet, and we have a staggering amount of work ahead. That means you have an unprecedented opportunity to put the global economy within everyone&#39;s reach while doing the most important work of your career.&lt;/p&gt;\n&lt;h3&gt;About the team&lt;/h3&gt;\n&lt;p&gt;The AI team is focused on one of Stripe’s most strategic growth areas: enabling the monetization and scaling of AI-native and AI-enabled businesses. We’re in a unique position – partnering with the world’s most ambitious AI companies (the likes of OpenAI, Anthropic, NVIDIA, etc) building on the frontier of artificial intelligence -- across infrastructure, foundation models, agents, and applications -- to help them grow and commercialize globally using Stripe’s full financial stack.&lt;/p&gt;\n&lt;p&gt;As part of Stripe’s GTM / Sales organization, this team works closely with Product, Engineering, and Marketing to shape Stripe’s AI GTM strategy and ensure that the world’s leading AI companies -- from early-stage innovators to the largest public players -- choose Stripe as their monetization platform.&lt;/p&gt;\n&lt;h3&gt;What you’ll do&lt;/h3&gt;\n&lt;ul&gt;\n&lt;li&gt;Work with existing Stripe customers in the AI Industry to develop and execute long-term sales strategies to expand Stripe’s revenue&lt;/li&gt;\n&lt;li&gt;Own the full sales cycle, from business case development, to deal structuring and negotiating, to close&lt;/li&gt;\n&lt;li&gt;Develop account plans and cross sell into your list of strategic AI customers, driving growth through expansion and new revenue streams&amp;nbsp;&lt;/li&gt;\n&lt;li&gt;Drive deal strategy and commercial negotiations for large, complex renewals&lt;/li&gt;\n&lt;li&gt;Develop relationships with executive stakeholders within your book of business, deeply understanding problems they are solving and helping drive to solutions&lt;/li&gt;\n&lt;li&gt;Be responsible for account mapping and coordinating effective meetings with the appropriate external stakeholders expanding the customer relationship&lt;/li&gt;\n&lt;li&gt;Lead and contribute to team projects to develop and refine our sales process and help shape the Stripe sales culture&lt;/li&gt;\n&lt;/ul&gt;\n&lt;h3&gt;Minimum requirements&lt;/h3&gt;\n&lt;ul&gt;\n&lt;li&gt;6+ years of sales experience, preferably selling a technical product, with a track record of top performance&lt;/li&gt;\n&lt;li&gt;2+ years leading complex sales cycles with Enterprise organizations required&lt;/li&gt;\n&lt;li&gt;Ability to understand technical requirements and craft solutions across multiple products&lt;/li&gt;\n&lt;li&gt;Ability to develop and execute account plans spanning multiple business units across complex organizations&lt;/li&gt;\n&lt;li&gt;A knack for working well with a wide range of people, both internally and externally&lt;/li&gt;\n&lt;li&gt;Strong presentation skills, particularly for in-person meetings with multiple stakeholders&lt;/li&gt;\n&lt;li&gt;Proven ability to lead complex negotiations involving bespoke commercial agreements&lt;/li&gt;\n&lt;li&gt;Superior verbal and written communication skills&lt;/li&gt;\n&lt;li&gt;Ability to operate in a highly ambiguous and fast-paced environment&lt;/li&gt;\n&lt;li&gt;Strong interest in technology and a deep understanding of the space&lt;/li&gt;\n&lt;/ul&gt;",
  "departments": [
    {
      "id": 380786,
      "name": "1654 Account Executives (AI)",
      "child_ids": [],
      "parent_id": 297997
    }
  ],
  "offices": [
    {
      "id": 65234,
      "name": "US",
      "location": null,
      "child_ids": [
        89623,
        249942
      ],
      "parent_id": 673
    }
  ],
  "ai_disclaimer": null,
  "include_ai_disclaimer": false,
  "ai_opt_out_request_url": "http://app.greenhouse.io/ai_opt_out_request/job_post/7954688/ai_opt_out"
}
```
</details>

<BR>

# Ashby
**Pattern**: `https://api.ashbyhq.com/posting-api/job-board/{job_board_name}?includeCompensation=true`
* jobUrl is the hosted posting; applyUrl is the application page.
* Single call returns every listed job with full descriptions — there is no separate per-job detail endpoint.
* `includeCompensation=true` adds a `compensation` object per job (tiers + summary components).
* Board name is in the careers URL `jobs.ashbyhq.com/{job_board_name}`.
<details><summary>Sample Outputs</summary>

includeCompensation=False
---
```json
{
  "id": "d46e79d4-43fd-477f-aa06-25b604715223",
  "title": "Strategy & Operations, Customer Success",
  "department": "Sales",
  "team": "Revenue Operations",
  "employmentType": "FullTime",
  "location": "Remote - US",
  "secondaryLocations": [
    {
      "location": "Remote - Canada",
      "address": {
        "postalAddress": {
          "addressRegion": "",
          "addressCountry": "Canada",
          "addressLocality": ""
        }
      }
    }
  ],
  "publishedAt": "2026-01-20T23:01:04.790+00:00",
  "isListed": true,
  "isRemote": true,
  "workplaceType": "Remote",
  "address": {
    "postalAddress": {
      "addressRegion": "",
      "addressCountry": "United States",
      "addressLocality": ""
    }
  },
  "jobUrl": "https://jobs.ashbyhq.com/ashby/d46e79d4-43fd-477f-aa06-25b604715223",
  "applyUrl": "https://jobs.ashbyhq.com/ashby/d46e79d4-43fd-477f-aa06-25b604715223/application",
  "descriptionHtml": "<p style=\"min-height:1.5em\"><strong>Role Overview</strong></p><p style=\"min-height:1.5em\">In this CSSO role, you will drive the strategy, processes, and data-driven insights that empower our Customer Success Team to deliver exceptional outcomes for our customers.  Reporting to the Senior Manager of Revenue Operations and partnering closely with Customer Success leadership, you will define strategies and execute improvements that optimize customer health, retention, and revenue growth.</p><p style=\"min-height:1.5em\">Our Customer Success team is consistently praised by our customers for their deep expertise, responsiveness, and dedication—a testament to how much we Care Deeply About Customers, one of our core Operating Principles. As we continue to scale, it’s critical that we preserve and enhance this high standard of customer care while ensuring our processes, tools, and insights evolve to support sustainable growth.</p><p style=\"min-height:1.5em\">You will play a crucial role in our operations, including process improvements, planning, and reporting.  This is both a strategic and tactical role in which you’ll design frameworks and programs, uncover actionable insights, and create data-informed recommendations across our Customer Success functions.</p><p style=\"min-height:1.5em\"></p><h3>What you'll do at Ashby:</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\"><strong>Operationalize CS Strategy: </strong>Develop and refine processes, tools, and workflows to scale Customer Success operations. This includes customer segmentation, onboarding workflows, lifecycle mapping, and playbook development.</p></li><li><p style=\"min-height:1.5em\"><strong>Analyze Business Dynamics: </strong>Partner with internal stakeholders on reporting &amp; analysis of metrics such as NRR, GRR, churn, product adoption, health score, and customer sentiment. Use these insights to provide early warnings and refine CS strategies.</p></li><li><p style=\"min-height:1.5em\"><strong>Optimizate the Customer Journey:</strong> Enhance the end-to-end customer journey, ensuring smooth transitions across all post-sales touch points from onboarding through support and renewal.</p></li><li><p style=\"min-height:1.5em\"><strong>Support Strategic Planning: </strong>Collaborate with Finance and CS leadership on planning cycles, including headcount planning, capacity modeling, and quota setting. Provide relevant historical data for informed decision making.</p></li><li><p style=\"min-height:1.5em\"><strong>Drive Process Improvements: </strong>Identify inefficiencies and implement improvements to CS workflows, customer onboarding, account transitions, and renewal processes. Implement &amp; deploy KPIs to measure performance against strategic initiatives.</p></li><li><p style=\"min-height:1.5em\"><strong>Optimize CS Systems: </strong>Define the business requirements to improve Customer Success efficiency, efficacy, and scalability. Partner with RevOps teammates to implement changes to tools (CRM, health scoring platforms, etc)</p></li><li><p style=\"min-height:1.5em\"><strong>Collaborate Cross-functionally</strong>: Ensure alignment with Sales, Product, and other teams to align resources with customer needs and forecasted growth.</p></li><li><p style=\"min-height:1.5em\"><strong>Support Change Management:</strong> Lead the roll-out and adoption of new processes or workflows to the CS team. Partner with Enablement on larger scale change programs.</p></li></ul><h3>You could be a great fit if you:</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">Are a self-starter, innately curious in your approach</p></li><li><p style=\"min-height:1.5em\">Bring both SaaS &amp; financial literacy, offering an informed perspective on CS benchmarks (e.g.ideal  book sizes)</p></li><li><p style=\"min-height:1.5em\">Have a strategic mindset, with the ability to distill data into actionable recommendations</p></li><li><p style=\"min-height:1.5em\">Are detail-oriented and committed to data accuracy and process quality</p></li><li><p style=\"min-height:1.5em\">Have experience in fast-growth SaaS environments, ideally in a Customer Success or Revenue Operations role</p></li><li><p style=\"min-height:1.5em\">Are skilled in CRM reporting, with preference for HubSpot experience</p></li><li><p style=\"min-height:1.5em\">Work autonomously, managing multiple tasks and deadlines effectively</p></li><li><p style=\"min-height:1.5em\">Are an excellent communicator, both verbally and in writing</p></li></ul><h3>This role may <em>not</em> be a fit if:</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">You’re unwilling to manage both high-level strategy and hands-on execution</p></li><li><p style=\"min-height:1.5em\">You’re uncomfortable with ambiguity and prefer structured, predefined assignments</p></li><li><p style=\"min-height:1.5em\">You prefer focused work on a single project at a time</p></li><li><p style=\"min-height:1.5em\">You prefer working within a single team rather than across multiple teams</p></li></ul><h3>Interview Process</h3><p style=\"min-height:1.5em\">Our interview process is thorough — we aim to ensure each person joining the team is the right fit for Ashby and will provide ample information to assess if Ashby is the right fit for you. The process for this role is as follows:</p><ol style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">Recruiter Screen with Talent Acquisition Team - 30 minutes</p></li><li><p style=\"min-height:1.5em\">Hiring Manager Interview / Career Deep Dive - 60 minutes</p></li><li><p style=\"min-height:1.5em\">Challenge Interview - 45 Minutes </p></li><li><p style=\"min-height:1.5em\">Final Round / Virtual Onsite - 2 hours</p></li></ol><h3><strong>About Ashby</strong></h3><p style=\"min-height:1.5em\">We’re building the next generation of enterprise software, and we’re starting with a suite of products that help talent leaders, recruiters, and hiring managers run their hiring process significantly better. Learn more on our website:<a target=\"_blank\" rel=\"noopener noreferrer nofollow\" href=\"https://www.ashbyhq.com/%EF%BF%BC%EF%BF%BCWe\"> </a><a target=\"_blank\" rel=\"noopener noreferrer nofollow\" href=\"https://www.ashbyhq.com/\"><u>https://www.ashbyhq.com/</u></a></p><p style=\"min-height:1.5em\">We are well funded and backed by great investors, including Y Combinator, Elad Gil and Lachy Groom. We have thousands of amazing customers including Snowflake, OpenAI, Shopify, Ramp, Notion, and Zapier (and many others who we cannot name!). Our growth and retention metrics are best-in-class among our peers: we have tens of millions in ARR, growing &gt;100% year over year, very low churn, and many years of runway. We’ll share more details once we meet. In short, it's the perfect time to join! 🚀</p><p style=\"min-height:1.5em\"></p><h3>Benefits</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">10-year exercise window for stock options. You shouldn’t feel pressure to purchase stock options if you leave Ashby —do it when you feel financially comfortable.</p></li><li><p style=\"min-height:1.5em\">Unlimited PTO with four weeks recommended per year. Expect “Vacation?” in our one-on-one agenda until you start taking it 😅.</p></li><li><p style=\"min-height:1.5em\">Twelve weeks of fully paid family leave in the US. We plan to expand this to employees in other countries as situations arise.</p></li><li><p style=\"min-height:1.5em\">Generous equipment, software, and office furniture budget. Get what you need to be happy and productive!</p></li><li><p style=\"min-height:1.5em\">$100/month education budget with more expensive items (like conferences) covered with manager approval.</p></li><li><p style=\"min-height:1.5em\">If you’re in the US, top-notch health insurance for you and your dependents, with all premiums covered by us.</p></li></ul><p style=\"min-height:1.5em\"><em>Ashby provides equal employment opportunities (EEO) to all employees and applicants for employment without regard to race, color, religion, sex, national origin, age, disability, genetics, sexual orientation, gender identity, or gender expression. We are committed to a diverse and inclusive workforce and welcome people from all backgrounds, experiences, perspectives, and abilities.</em></p><p style=\"min-height:1.5em\"><em>Ashby is committed to a fair and transparent hiring process. We confirm that this advertisement is for an active, existing vacancy within our organization. Please be advised that we may use artificial intelligence-driven tools to assist our recruitment team in screening, assessing, and selecting candidates for this position.</em></p>",
  "descriptionPlain": "Role Overview\n\nIn this CSSO role, you will drive the strategy, processes, and data-driven insights that empower our Customer Success Team to deliver exceptional outcomes for our customers. Reporting to the Senior Manager of Revenue Operations and partnering closely with Customer Success leadership, you will define strategies and execute improvements that optimize customer health, retention, and revenue growth.\n\nOur Customer Success team is consistently praised by our customers for their deep expertise, responsiveness, and dedication—a testament to how much we Care Deeply About Customers, one of our core Operating Principles. As we continue to scale, it’s critical that we preserve and enhance this high standard of customer care while ensuring our processes, tools, and insights evolve to support sustainable growth.\n\nYou will play a crucial role in our operations, including process improvements, planning, and reporting. This is both a strategic and tactical role in which you’ll design frameworks and programs, uncover actionable insights, and create data-informed recommendations across our Customer Success functions.\n\n\n\n\nWHAT YOU'LL DO AT ASHBY:\n\n - Operationalize CS Strategy: Develop and refine processes, tools, and workflows to scale Customer Success operations. This includes customer segmentation, onboarding workflows, lifecycle mapping, and playbook development.\n\n - Analyze Business Dynamics: Partner with internal stakeholders on reporting & analysis of metrics such as NRR, GRR, churn, product adoption, health score, and customer sentiment. Use these insights to provide early warnings and refine CS strategies.\n\n - Optimizate the Customer Journey: Enhance the end-to-end customer journey, ensuring smooth transitions across all post-sales touch points from onboarding through support and renewal.\n\n - Support Strategic Planning: Collaborate with Finance and CS leadership on planning cycles, including headcount planning, capacity modeling, and quota setting. Provide relevant historical data for informed decision making.\n\n - Drive Process Improvements: Identify inefficiencies and implement improvements to CS workflows, customer onboarding, account transitions, and renewal processes. Implement & deploy KPIs to measure performance against strategic initiatives.\n\n - Optimize CS Systems: Define the business requirements to improve Customer Success efficiency, efficacy, and scalability. Partner with RevOps teammates to implement changes to tools (CRM, health scoring platforms, etc)\n\n - Collaborate Cross-functionally: Ensure alignment with Sales, Product, and other teams to align resources with customer needs and forecasted growth.\n\n - Support Change Management: Lead the roll-out and adoption of new processes or workflows to the CS team. Partner with Enablement on larger scale change programs.\n\n\nYOU COULD BE A GREAT FIT IF YOU:\n\n - Are a self-starter, innately curious in your approach\n\n - Bring both SaaS & financial literacy, offering an informed perspective on CS benchmarks (e.g.ideal book sizes)\n\n - Have a strategic mindset, with the ability to distill data into actionable recommendations\n\n - Are detail-oriented and committed to data accuracy and process quality\n\n - Have experience in fast-growth SaaS environments, ideally in a Customer Success or Revenue Operations role\n\n - Are skilled in CRM reporting, with preference for HubSpot experience\n\n - Work autonomously, managing multiple tasks and deadlines effectively\n\n - Are an excellent communicator, both verbally and in writing\n\n\nTHIS ROLE MAY NOT BE A FIT IF:\n\n - You’re unwilling to manage both high-level strategy and hands-on execution\n\n - You’re uncomfortable with ambiguity and prefer structured, predefined assignments\n\n - You prefer focused work on a single project at a time\n\n - You prefer working within a single team rather than across multiple teams\n\n\nINTERVIEW PROCESS\n\nOur interview process is thorough — we aim to ensure each person joining the team is the right fit for Ashby and will provide ample information to assess if Ashby is the right fit for you. The process for this role is as follows:\n\n 1. Recruiter Screen with Talent Acquisition Team - 30 minutes\n\n 2. Hiring Manager Interview / Career Deep Dive - 60 minutes\n\n 3. Challenge Interview - 45 Minutes\n\n 4. Final Round / Virtual Onsite - 2 hours\n\n\nABOUT ASHBY\n\nWe’re building the next generation of enterprise software, and we’re starting with a suite of products that help talent leaders, recruiters, and hiring managers run their hiring process significantly better. Learn more on our website: https://www.ashbyhq.com/%EF%BF%BC%EF%BF%BCWehttps://www.ashbyhq.com/\n\nWe are well funded and backed by great investors, including Y Combinator, Elad Gil and Lachy Groom. We have thousands of amazing customers including Snowflake, OpenAI, Shopify, Ramp, Notion, and Zapier (and many others who we cannot name!). Our growth and retention metrics are best-in-class among our peers: we have tens of millions in ARR, growing >100% year over year, very low churn, and many years of runway. We’ll share more details once we meet. In short, it's the perfect time to join! 🚀\n\n\n\n\nBENEFITS\n\n - 10-year exercise window for stock options. You shouldn’t feel pressure to purchase stock options if you leave Ashby —do it when you feel financially comfortable.\n\n - Unlimited PTO with four weeks recommended per year. Expect “Vacation?” in our one-on-one agenda until you start taking it 😅.\n\n - Twelve weeks of fully paid family leave in the US. We plan to expand this to employees in other countries as situations arise.\n\n - Generous equipment, software, and office furniture budget. Get what you need to be happy and productive!\n\n - $100/month education budget with more expensive items (like conferences) covered with manager approval.\n\n - If you’re in the US, top-notch health insurance for you and your dependents, with all premiums covered by us.\n\nAshby provides equal employment opportunities (EEO) to all employees and applicants for employment without regard to race, color, religion, sex, national origin, age, disability, genetics, sexual orientation, gender identity, or gender expression. We are committed to a diverse and inclusive workforce and welcome people from all backgrounds, experiences, perspectives, and abilities.\n\nAshby is committed to a fair and transparent hiring process. We confirm that this advertisement is for an active, existing vacancy within our organization. Please be advised that we may use artificial intelligence-driven tools to assist our recruitment team in screening, assessing, and selecting candidates for this position."
}
```
includeCompensation=True
---
```json
{
  "id": "d46e79d4-43fd-477f-aa06-25b604715223",
  "title": "Strategy & Operations, Customer Success",
  "department": "Sales",
  "team": "Revenue Operations",
  "employmentType": "FullTime",
  "location": "Remote - US",
  "shouldDisplayCompensationOnJobPostings": true,
  "secondaryLocations": [
    {
      "location": "Remote - Canada",
      "address": {
        "postalAddress": {
          "addressRegion": "",
          "addressCountry": "Canada",
          "addressLocality": ""
        }
      }
    }
  ],
  "publishedAt": "2026-01-20T23:01:04.790+00:00",
  "isListed": true,
  "isRemote": true,
  "workplaceType": "Remote",
  "address": {
    "postalAddress": {
      "addressRegion": "",
      "addressCountry": "United States",
      "addressLocality": ""
    }
  },
  "jobUrl": "https://jobs.ashbyhq.com/ashby/d46e79d4-43fd-477f-aa06-25b604715223",
  "applyUrl": "https://jobs.ashbyhq.com/ashby/d46e79d4-43fd-477f-aa06-25b604715223/application",
  "descriptionHtml": "<p style=\"min-height:1.5em\"><strong>Role Overview</strong></p><p style=\"min-height:1.5em\">In this CSSO role, you will drive the strategy, processes, and data-driven insights that empower our Customer Success Team to deliver exceptional outcomes for our customers.  Reporting to the Senior Manager of Revenue Operations and partnering closely with Customer Success leadership, you will define strategies and execute improvements that optimize customer health, retention, and revenue growth.</p><p style=\"min-height:1.5em\">Our Customer Success team is consistently praised by our customers for their deep expertise, responsiveness, and dedication—a testament to how much we Care Deeply About Customers, one of our core Operating Principles. As we continue to scale, it’s critical that we preserve and enhance this high standard of customer care while ensuring our processes, tools, and insights evolve to support sustainable growth.</p><p style=\"min-height:1.5em\">You will play a crucial role in our operations, including process improvements, planning, and reporting.  This is both a strategic and tactical role in which you’ll design frameworks and programs, uncover actionable insights, and create data-informed recommendations across our Customer Success functions.</p><p style=\"min-height:1.5em\"></p><h3>What you'll do at Ashby:</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\"><strong>Operationalize CS Strategy: </strong>Develop and refine processes, tools, and workflows to scale Customer Success operations. This includes customer segmentation, onboarding workflows, lifecycle mapping, and playbook development.</p></li><li><p style=\"min-height:1.5em\"><strong>Analyze Business Dynamics: </strong>Partner with internal stakeholders on reporting &amp; analysis of metrics such as NRR, GRR, churn, product adoption, health score, and customer sentiment. Use these insights to provide early warnings and refine CS strategies.</p></li><li><p style=\"min-height:1.5em\"><strong>Optimizate the Customer Journey:</strong> Enhance the end-to-end customer journey, ensuring smooth transitions across all post-sales touch points from onboarding through support and renewal.</p></li><li><p style=\"min-height:1.5em\"><strong>Support Strategic Planning: </strong>Collaborate with Finance and CS leadership on planning cycles, including headcount planning, capacity modeling, and quota setting. Provide relevant historical data for informed decision making.</p></li><li><p style=\"min-height:1.5em\"><strong>Drive Process Improvements: </strong>Identify inefficiencies and implement improvements to CS workflows, customer onboarding, account transitions, and renewal processes. Implement &amp; deploy KPIs to measure performance against strategic initiatives.</p></li><li><p style=\"min-height:1.5em\"><strong>Optimize CS Systems: </strong>Define the business requirements to improve Customer Success efficiency, efficacy, and scalability. Partner with RevOps teammates to implement changes to tools (CRM, health scoring platforms, etc)</p></li><li><p style=\"min-height:1.5em\"><strong>Collaborate Cross-functionally</strong>: Ensure alignment with Sales, Product, and other teams to align resources with customer needs and forecasted growth.</p></li><li><p style=\"min-height:1.5em\"><strong>Support Change Management:</strong> Lead the roll-out and adoption of new processes or workflows to the CS team. Partner with Enablement on larger scale change programs.</p></li></ul><h3>You could be a great fit if you:</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">Are a self-starter, innately curious in your approach</p></li><li><p style=\"min-height:1.5em\">Bring both SaaS &amp; financial literacy, offering an informed perspective on CS benchmarks (e.g.ideal  book sizes)</p></li><li><p style=\"min-height:1.5em\">Have a strategic mindset, with the ability to distill data into actionable recommendations</p></li><li><p style=\"min-height:1.5em\">Are detail-oriented and committed to data accuracy and process quality</p></li><li><p style=\"min-height:1.5em\">Have experience in fast-growth SaaS environments, ideally in a Customer Success or Revenue Operations role</p></li><li><p style=\"min-height:1.5em\">Are skilled in CRM reporting, with preference for HubSpot experience</p></li><li><p style=\"min-height:1.5em\">Work autonomously, managing multiple tasks and deadlines effectively</p></li><li><p style=\"min-height:1.5em\">Are an excellent communicator, both verbally and in writing</p></li></ul><h3>This role may <em>not</em> be a fit if:</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">You’re unwilling to manage both high-level strategy and hands-on execution</p></li><li><p style=\"min-height:1.5em\">You’re uncomfortable with ambiguity and prefer structured, predefined assignments</p></li><li><p style=\"min-height:1.5em\">You prefer focused work on a single project at a time</p></li><li><p style=\"min-height:1.5em\">You prefer working within a single team rather than across multiple teams</p></li></ul><h3>Interview Process</h3><p style=\"min-height:1.5em\">Our interview process is thorough — we aim to ensure each person joining the team is the right fit for Ashby and will provide ample information to assess if Ashby is the right fit for you. The process for this role is as follows:</p><ol style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">Recruiter Screen with Talent Acquisition Team - 30 minutes</p></li><li><p style=\"min-height:1.5em\">Hiring Manager Interview / Career Deep Dive - 60 minutes</p></li><li><p style=\"min-height:1.5em\">Challenge Interview - 45 Minutes </p></li><li><p style=\"min-height:1.5em\">Final Round / Virtual Onsite - 2 hours</p></li></ol><h3><strong>About Ashby</strong></h3><p style=\"min-height:1.5em\">We’re building the next generation of enterprise software, and we’re starting with a suite of products that help talent leaders, recruiters, and hiring managers run their hiring process significantly better. Learn more on our website:<a target=\"_blank\" rel=\"noopener noreferrer nofollow\" href=\"https://www.ashbyhq.com/%EF%BF%BC%EF%BF%BCWe\"> </a><a target=\"_blank\" rel=\"noopener noreferrer nofollow\" href=\"https://www.ashbyhq.com/\"><u>https://www.ashbyhq.com/</u></a></p><p style=\"min-height:1.5em\">We are well funded and backed by great investors, including Y Combinator, Elad Gil and Lachy Groom. We have thousands of amazing customers including Snowflake, OpenAI, Shopify, Ramp, Notion, and Zapier (and many others who we cannot name!). Our growth and retention metrics are best-in-class among our peers: we have tens of millions in ARR, growing &gt;100% year over year, very low churn, and many years of runway. We’ll share more details once we meet. In short, it's the perfect time to join! 🚀</p><p style=\"min-height:1.5em\"></p><h3>Benefits</h3><ul style=\"min-height:1.5em\"><li><p style=\"min-height:1.5em\">10-year exercise window for stock options. You shouldn’t feel pressure to purchase stock options if you leave Ashby —do it when you feel financially comfortable.</p></li><li><p style=\"min-height:1.5em\">Unlimited PTO with four weeks recommended per year. Expect “Vacation?” in our one-on-one agenda until you start taking it 😅.</p></li><li><p style=\"min-height:1.5em\">Twelve weeks of fully paid family leave in the US. We plan to expand this to employees in other countries as situations arise.</p></li><li><p style=\"min-height:1.5em\">Generous equipment, software, and office furniture budget. Get what you need to be happy and productive!</p></li><li><p style=\"min-height:1.5em\">$100/month education budget with more expensive items (like conferences) covered with manager approval.</p></li><li><p style=\"min-height:1.5em\">If you’re in the US, top-notch health insurance for you and your dependents, with all premiums covered by us.</p></li></ul><p style=\"min-height:1.5em\"><em>Ashby provides equal employment opportunities (EEO) to all employees and applicants for employment without regard to race, color, religion, sex, national origin, age, disability, genetics, sexual orientation, gender identity, or gender expression. We are committed to a diverse and inclusive workforce and welcome people from all backgrounds, experiences, perspectives, and abilities.</em></p><p style=\"min-height:1.5em\"><em>Ashby is committed to a fair and transparent hiring process. We confirm that this advertisement is for an active, existing vacancy within our organization. Please be advised that we may use artificial intelligence-driven tools to assist our recruitment team in screening, assessing, and selecting candidates for this position.</em></p>",
  "descriptionPlain": "Role Overview\n\nIn this CSSO role, you will drive the strategy, processes, and data-driven insights that empower our Customer Success Team to deliver exceptional outcomes for our customers. Reporting to the Senior Manager of Revenue Operations and partnering closely with Customer Success leadership, you will define strategies and execute improvements that optimize customer health, retention, and revenue growth.\n\nOur Customer Success team is consistently praised by our customers for their deep expertise, responsiveness, and dedication—a testament to how much we Care Deeply About Customers, one of our core Operating Principles. As we continue to scale, it’s critical that we preserve and enhance this high standard of customer care while ensuring our processes, tools, and insights evolve to support sustainable growth.\n\nYou will play a crucial role in our operations, including process improvements, planning, and reporting. This is both a strategic and tactical role in which you’ll design frameworks and programs, uncover actionable insights, and create data-informed recommendations across our Customer Success functions.\n\n\n\n\nWHAT YOU'LL DO AT ASHBY:\n\n - Operationalize CS Strategy: Develop and refine processes, tools, and workflows to scale Customer Success operations. This includes customer segmentation, onboarding workflows, lifecycle mapping, and playbook development.\n\n - Analyze Business Dynamics: Partner with internal stakeholders on reporting & analysis of metrics such as NRR, GRR, churn, product adoption, health score, and customer sentiment. Use these insights to provide early warnings and refine CS strategies.\n\n - Optimizate the Customer Journey: Enhance the end-to-end customer journey, ensuring smooth transitions across all post-sales touch points from onboarding through support and renewal.\n\n - Support Strategic Planning: Collaborate with Finance and CS leadership on planning cycles, including headcount planning, capacity modeling, and quota setting. Provide relevant historical data for informed decision making.\n\n - Drive Process Improvements: Identify inefficiencies and implement improvements to CS workflows, customer onboarding, account transitions, and renewal processes. Implement & deploy KPIs to measure performance against strategic initiatives.\n\n - Optimize CS Systems: Define the business requirements to improve Customer Success efficiency, efficacy, and scalability. Partner with RevOps teammates to implement changes to tools (CRM, health scoring platforms, etc)\n\n - Collaborate Cross-functionally: Ensure alignment with Sales, Product, and other teams to align resources with customer needs and forecasted growth.\n\n - Support Change Management: Lead the roll-out and adoption of new processes or workflows to the CS team. Partner with Enablement on larger scale change programs.\n\n\nYOU COULD BE A GREAT FIT IF YOU:\n\n - Are a self-starter, innately curious in your approach\n\n - Bring both SaaS & financial literacy, offering an informed perspective on CS benchmarks (e.g.ideal book sizes)\n\n - Have a strategic mindset, with the ability to distill data into actionable recommendations\n\n - Are detail-oriented and committed to data accuracy and process quality\n\n - Have experience in fast-growth SaaS environments, ideally in a Customer Success or Revenue Operations role\n\n - Are skilled in CRM reporting, with preference for HubSpot experience\n\n - Work autonomously, managing multiple tasks and deadlines effectively\n\n - Are an excellent communicator, both verbally and in writing\n\n\nTHIS ROLE MAY NOT BE A FIT IF:\n\n - You’re unwilling to manage both high-level strategy and hands-on execution\n\n - You’re uncomfortable with ambiguity and prefer structured, predefined assignments\n\n - You prefer focused work on a single project at a time\n\n - You prefer working within a single team rather than across multiple teams\n\n\nINTERVIEW PROCESS\n\nOur interview process is thorough — we aim to ensure each person joining the team is the right fit for Ashby and will provide ample information to assess if Ashby is the right fit for you. The process for this role is as follows:\n\n 1. Recruiter Screen with Talent Acquisition Team - 30 minutes\n\n 2. Hiring Manager Interview / Career Deep Dive - 60 minutes\n\n 3. Challenge Interview - 45 Minutes\n\n 4. Final Round / Virtual Onsite - 2 hours\n\n\nABOUT ASHBY\n\nWe’re building the next generation of enterprise software, and we’re starting with a suite of products that help talent leaders, recruiters, and hiring managers run their hiring process significantly better. Learn more on our website: https://www.ashbyhq.com/%EF%BF%BC%EF%BF%BCWehttps://www.ashbyhq.com/\n\nWe are well funded and backed by great investors, including Y Combinator, Elad Gil and Lachy Groom. We have thousands of amazing customers including Snowflake, OpenAI, Shopify, Ramp, Notion, and Zapier (and many others who we cannot name!). Our growth and retention metrics are best-in-class among our peers: we have tens of millions in ARR, growing >100% year over year, very low churn, and many years of runway. We’ll share more details once we meet. In short, it's the perfect time to join! 🚀\n\n\n\n\nBENEFITS\n\n - 10-year exercise window for stock options. You shouldn’t feel pressure to purchase stock options if you leave Ashby —do it when you feel financially comfortable.\n\n - Unlimited PTO with four weeks recommended per year. Expect “Vacation?” in our one-on-one agenda until you start taking it 😅.\n\n - Twelve weeks of fully paid family leave in the US. We plan to expand this to employees in other countries as situations arise.\n\n - Generous equipment, software, and office furniture budget. Get what you need to be happy and productive!\n\n - $100/month education budget with more expensive items (like conferences) covered with manager approval.\n\n - If you’re in the US, top-notch health insurance for you and your dependents, with all premiums covered by us.\n\nAshby provides equal employment opportunities (EEO) to all employees and applicants for employment without regard to race, color, religion, sex, national origin, age, disability, genetics, sexual orientation, gender identity, or gender expression. We are committed to a diverse and inclusive workforce and welcome people from all backgrounds, experiences, perspectives, and abilities.\n\nAshby is committed to a fair and transparent hiring process. We confirm that this advertisement is for an active, existing vacancy within our organization. Please be advised that we may use artificial intelligence-driven tools to assist our recruitment team in screening, assessing, and selecting candidates for this position.",
  "compensation": {
    "compensationTierSummary": "$140K – $180K • Offers Equity",
    "scrapeableCompensationSalarySummary": "$140K - $180K",
    "compensationTiers": [
      {
        "id": "aa64899a-c27e-49bf-95c4-a9b7cfce8315",
        "tierSummary": "$140K – $180K • Offers Equity",
        "title": null,
        "additionalInformation": null,
        "components": [
          {
            "id": "e64446b5-5d9b-49ed-85d7-a1e0e686f650",
            "summary": "$140K – $180K",
            "compensationType": "Salary",
            "interval": "1 YEAR",
            "currencyCode": "USD",
            "minValue": 140000,
            "maxValue": 180000
          },
          {
            "id": "37af775a-88e2-46af-8394-ca4dae255102",
            "summary": "Offers Equity",
            "compensationType": "EquityPercentage",
            "interval": "NONE",
            "currencyCode": null,
            "minValue": null,
            "maxValue": null
          }
        ]
      }
    ],
    "summaryComponents": [
      {
        "compensationType": "Salary",
        "interval": "1 YEAR",
        "currencyCode": "USD",
        "minValue": 140000,
        "maxValue": 180000
      },
      {
        "compensationType": "EquityPercentage",
        "interval": "NONE",
        "currencyCode": null,
        "minValue": null,
        "maxValue": null
      }
    ]
  }
}
```
</details>

<BR>

# Lever
**Pattern**: `https://api.lever.co/v0/postings/{site}?mode=json`
* Returns a JSON array of postings, full descriptions included; paginate with `&limit=N&skip=N`.
* Detail: `/v0/postings/{site}/{posting_id}` — returns the exact same object shape as a list element (verified: identical 17 fields), so the list alone is sufficient.
* hostedUrl is the hosted posting; applyUrl is the application page.
* Site name is in the careers URL `jobs.lever.co/{site}` (EU tenants: `jobs.eu.lever.co/{site}` → `api.eu.lever.co`).

<details><summary>Sample Outputs</summary>

GET /v0/postings/gohighlevel?mode=json (one element of the array)
---
```json
{
  "additionalPlain": "EEO Statement:\nThe company is an Equal Opportunity Employer. As an employer subject to affirmative action regulations, we invite you to voluntarily provide the following demographic information. This information is used solely for compliance with government recordkeeping, reporting, and other legal requirements. Providing this information is voluntary and refusal to do so will not affect your application status. This data will be kept separate from your application and will not be used in the hiring decision.\n\nWe encourage you to review our Privacy Policy before submitting your application\n",
  "additional": "<div>\n<p><strong>EEO Statement:</strong></p>\n<p>The company is an Equal Opportunity Employer. As an employer subject to affirmative action regulations, we invite you to voluntarily provide the following demographic information. This information is used solely for compliance with government recordkeeping, reporting, and other legal requirements. Providing this information is voluntary and refusal to do so will not affect your application status. This data will be kept separate from your application and will not be used in the hiring decision.<br><br>We encourage you to review our <a href=\"https://www.gohighlevel.com/job-applicant-privacy-notice\">Privacy Policy</a> before submitting your application</p>\n</div>",
  "categories": {
    "commitment": "EE Full-Time",
    "department": "Finance",
    "location": "India",
    "team": "Accounting",
    "allLocations": [
      "India"
    ]
  },
  "createdAt": 1784651763463,
  "descriptionPlain": "About us\nHighLevel is an AI-powered business operating system that gives agencies, entrepreneurs and SMBs the infrastructure to build, automate and scale. Today, HighLevel supports SMBs across 150+ countries, fueling community-driven growth rooted in real customer outcomes.\nTo date, businesses operating on HighLevel have generated over $7 billion in ecosystem value, demonstrating the impact of shared infrastructure at scale. By centralizing conversations, automation and intelligence into one system, we help businesses move faster, reduce complexity and execute efficiently.\nBehind the platform, HighLevel powers more than 4 billion API hits and 2.5 billion message events daily. With 250 terabytes of distributed data, 250+ microservices and over 1 million domain names supported, our architecture is built for performance, resilience and long-term scalability.\n\nOur people\nWith over 2,000 team members across 10+ countries, HighLevel operates as a global, remote-first organization built for speed and ownership. We value initiative, clarity and execution, creating space for ambitious people to build systems that support millions of businesses worldwide. Here, innovation thrives, ideas are celebrated and people come first, no matter where they call home.\n\nOur impact\nEvery month, HighLevel enables more than 1.5 billion messages, 200 million leads and 20 million conversations for the more than 1 million businesses we support. Behind those numbers are real people building independence, expanding opportunity and creating measurable impact. We’re proud to be a part of that.\nLearn more about us on our YouTube Channel or Blog Posts \nWho You Are: \nHighLevel is looking for an experienced accounts payable specialist who is detail-oriented, organized, and committed to ensuring accurate and timely processing of invoices and payments. You have strong communication skills enabling you to collaborate effectively with vendors and internal teams, and you are proficient in accounting software and Excel. You are proactive, reliable, and dedicated to maintaining the confidentiality of financial information while continually seeking to improve processes and enhance efficiency. You are customer service oriented and committed to working with vendors and internal budget managers.\n",
  "description": "<p><strong>About us</strong><br aria-hidden=\"true\">HighLevel is an AI-powered business operating system that gives agencies, entrepreneurs and SMBs the infrastructure to build, automate and scale. Today, HighLevel supports SMBs across 150+ countries, fueling community-driven growth rooted in real customer outcomes.<br aria-hidden=\"true\">To date, businesses operating on HighLevel have generated over $7 billion in ecosystem value, demonstrating the impact of shared infrastructure at scale. By centralizing conversations, automation and intelligence into one system, we help businesses move faster, reduce complexity and execute efficiently.<br aria-hidden=\"true\">Behind the platform, HighLevel powers more than 4 billion API hits and 2.5 billion message events daily. With 250 terabytes of distributed data, 250+ microservices and over 1 million domain names supported, our architecture is built for performance, resilience and long-term scalability.<br><br><strong>Our people</strong><br aria-hidden=\"true\">With over 2,000 team members across 10+ countries, HighLevel operates as a global, remote-first organization built for speed and ownership. We value initiative, clarity and execution, creating space for ambitious people to build systems that support millions of businesses worldwide. Here, innovation thrives, ideas are celebrated and people come first, no matter where they call home.<br><br><strong>Our impact</strong><br aria-hidden=\"true\">Every month, HighLevel enables more than 1.5 billion messages, 200 million leads and 20 million conversations for the more than 1 million businesses we support. Behind those numbers are real people building independence, expanding opportunity and creating measurable impact. We’re proud to be a part of that.<br aria-hidden=\"true\">Learn more about us on our <span style=\"text-decoration: underline;\"><span draggable=\"true\"><a rel=\"noopener noreferrer\" href=\"https://www.youtube.com/channel/UCXFiV4qDX5ipE-DQcsm1j4g\">YouTube Channel</a></span></span> or <span style=\"text-decoration: underline;\"><span draggable=\"true\"><a rel=\"noopener noreferrer\" href=\"https://blog.gohighlevel.com/general-atlantic-joins-highlevel/\">Blog Posts</a></span></span>&nbsp;</p>\n<p><strong>Who You Are:&nbsp;</strong></p>\n<p>HighLevel is looking for an experienced accounts payable specialist who is detail-oriented, organized, and committed to ensuring accurate and timely processing of invoices and payments. You have strong communication skills enabling you to collaborate effectively with vendors and internal teams, and you are proficient in accounting software and Excel. You are proactive, reliable, and dedicated to maintaining the confidentiality of financial information while continually seeking to improve processes and enhance efficiency. You are customer service oriented and committed to working with vendors and internal budget managers.</p>",
  "id": "cdaaed4d-638d-4348-b2ca-f539a5f0fef5",
  "lists": [
    {
      "text": "What You’ll Be Doing:",
      "content": "<div>\n\n<li>\n<p><strong>Vendor Invoice Processing:</strong> Review, code, and process invoices accurately and promptly in accordance with company policies and procedures. Work with budget managers to get invoices approved according to the company policies. Reconcile 2-way and 3-way matching against purchase orders.&nbsp;</p>\n</li>\n<li>\n<p><strong>Vendor Onboarding Management:</strong> Maintain vendor records, assist with onboarding new vendors, troubleshooting onboarding issues, and review records closely for potential fraud risk&nbsp;</p>\n</li>\n<li>\n<p><strong>Accounts Payable Inbox Management:</strong> Maintain communication with internal stakeholders and vendors in a timely manner, address inquiries and discrepancies, and resolve payment issues.&nbsp;</p>\n</li>\n<li>\n<p><strong>Payment Processing: </strong>Ensure payments are processed through Ramp and other payment processing methods on a weekly basis.</p>\n</li>\n<li>\n<p><strong>Expense Reporting:</strong> Reconcile India employee expense reports and credit card statements, verify compliance with company policies, and facilitate timely reimbursements. Work with employees on any non-compliance.</p>\n</li>\n<li>\n<p><strong>Account Reconciliation:</strong> Reconcile accounts payable transactions, including vendor statements and discrepancies, to ensure accuracy and completeness. Review daily outgoing cash to ensure that all manual payments are accounted for and recorded.&nbsp;</p>\n</li>\n<li>\n<p><strong>Month-end Close:</strong> Assist with month-end closing activities by preparing journal entries, accruals, and account reconciliations related to accounts payable.</p>\n</li>\n<li>\n<p><strong>Audit Support:</strong> Provide support for internal and external audits by preparing documentation, responding to inquiries, and assisting with audit procedures related to accounts payable.</p>\n</li>\n<li>\n<p><strong>Process Improvement:</strong> Identify opportunities for process improvements in accounts payable workflows to enhance efficiency, accuracy, and internal controls.</p>\n</li>\n\n</div>"
    },
    {
      "text": "What You’ll Bring:",
      "content": "<div>\n<p>To perform this job successfully, an individual must be able to perform each essential duty satisfactorily. The requirements listed below are representative of the knowledge, skill, and/or ability required. Reasonable accommodations may be made to enable individuals with disabilities to perform the essential functions.&nbsp;</p>\n<p><strong>Experience/Education/Certifications Required:</strong></p>\n\n<li>\n<p>Bachelor's degree in Accounting, Finance, or related field required.</p>\n</li>\n<li>\n<p>Minimum of 2+ years of experience as an Accounts Payable Specialist or similar role with familiarity with P2P end-to-end processes</p>\n</li>\n<li>\n<p>Hands-on experience with expense management platforms such as Ramp, Expensify, Brex, or similar systems; experience with Ramp strongly preferred.</p>\n</li>\n<li>\n<p>Experience with ERP or accounting systems such as NetSuite, Sage Intacct, SAP, or similar platforms; experience with NetSuite strongly preferred</p>\n</li>\n<li>\n<p>Proficient in Microsoft Excel and Google Sheets.</p>\n</li>\n<li>\n<p>Strong attention to detail, accuracy, and organizational skills.</p>\n</li>\n<li>\n<p>Ability to prioritize tasks effectively and meet deadlines in a fast-paced environment.</p>\n</li>\n<li>\n<p>Excellent communication and interpersonal skills, with the ability to collaborate. effectively with vendors and internal stakeholders.</p>\n</li>\n<li>\n<p>Experience with process improvement initiatives is a plus.</p>\n</li>\n<li>\n<p>Self-starter who is proactive, takes initiative and follows through on tasks in a timely manner.</p>\n</li>\n<li>\n<p>Ability to adapt quickly to changing priorities.&nbsp;</p>\n</li>\n<li>\n<p>Excellent organization and time management skills.</p>\n</li>\n\n</div>"
    }
  ],
  "text": "Accounts Payable Specialist II",
  "country": "IN",
  "workplaceType": "remote",
  "opening": "",
  "openingPlain": "",
  "descriptionBody": "<p><strong>About us</strong><br aria-hidden=\"true\">HighLevel is an AI-powered business operating system that gives agencies, entrepreneurs and SMBs the infrastructure to build, automate and scale. Today, HighLevel supports SMBs across 150+ countries, fueling community-driven growth rooted in real customer outcomes.<br aria-hidden=\"true\">To date, businesses operating on HighLevel have generated over $7 billion in ecosystem value, demonstrating the impact of shared infrastructure at scale. By centralizing conversations, automation and intelligence into one system, we help businesses move faster, reduce complexity and execute efficiently.<br aria-hidden=\"true\">Behind the platform, HighLevel powers more than 4 billion API hits and 2.5 billion message events daily. With 250 terabytes of distributed data, 250+ microservices and over 1 million domain names supported, our architecture is built for performance, resilience and long-term scalability.<br><br><strong>Our people</strong><br aria-hidden=\"true\">With over 2,000 team members across 10+ countries, HighLevel operates as a global, remote-first organization built for speed and ownership. We value initiative, clarity and execution, creating space for ambitious people to build systems that support millions of businesses worldwide. Here, innovation thrives, ideas are celebrated and people come first, no matter where they call home.<br><br><strong>Our impact</strong><br aria-hidden=\"true\">Every month, HighLevel enables more than 1.5 billion messages, 200 million leads and 20 million conversations for the more than 1 million businesses we support. Behind those numbers are real people building independence, expanding opportunity and creating measurable impact. We&rsquo;re proud to be a part of that.<br aria-hidden=\"true\">Learn more about us on our <span style=\"text-decoration: underline;\"><span draggable=\"true\"><a href=\"https://www.youtube.com/channel/UCXFiV4qDX5ipE-DQcsm1j4g\" target=\"_blank\" rel=\"noopener noreferrer\">YouTube Channel</a></span></span> or <span style=\"text-decoration: underline;\"><span draggable=\"true\"><a href=\"https://blog.gohighlevel.com/general-atlantic-joins-highlevel/\" target=\"_blank\" rel=\"noopener noreferrer\">Blog Posts</a></span></span>&nbsp;</p>\n<p><strong>Who You Are:&nbsp;</strong></p>\n<p>HighLevel is looking for an experienced accounts payable specialist who is detail-oriented, organized, and committed to ensuring accurate and timely processing of invoices and payments. You have strong communication skills enabling you to collaborate effectively with vendors and internal teams, and you are proficient in accounting software and Excel. You are proactive, reliable, and dedicated to maintaining the confidentiality of financial information while continually seeking to improve processes and enhance efficiency. You are customer service oriented and committed to working with vendors and internal budget managers.</p>",
  "descriptionBodyPlain": "About us\nHighLevel is an AI-powered business operating system that gives agencies, entrepreneurs and SMBs the infrastructure to build, automate and scale. Today, HighLevel supports SMBs across 150+ countries, fueling community-driven growth rooted in real customer outcomes.\nTo date, businesses operating on HighLevel have generated over $7 billion in ecosystem value, demonstrating the impact of shared infrastructure at scale. By centralizing conversations, automation and intelligence into one system, we help businesses move faster, reduce complexity and execute efficiently.\nBehind the platform, HighLevel powers more than 4 billion API hits and 2.5 billion message events daily. With 250 terabytes of distributed data, 250+ microservices and over 1 million domain names supported, our architecture is built for performance, resilience and long-term scalability.\n\nOur people\nWith over 2,000 team members across 10+ countries, HighLevel operates as a global, remote-first organization built for speed and ownership. We value initiative, clarity and execution, creating space for ambitious people to build systems that support millions of businesses worldwide. Here, innovation thrives, ideas are celebrated and people come first, no matter where they call home.\n\nOur impact\nEvery month, HighLevel enables more than 1.5 billion messages, 200 million leads and 20 million conversations for the more than 1 million businesses we support. Behind those numbers are real people building independence, expanding opportunity and creating measurable impact. We’re proud to be a part of that.\nLearn more about us on our YouTube Channel or Blog Posts \nWho You Are: \nHighLevel is looking for an experienced accounts payable specialist who is detail-oriented, organized, and committed to ensuring accurate and timely processing of invoices and payments. You have strong communication skills enabling you to collaborate effectively with vendors and internal teams, and you are proficient in accounting software and Excel. You are proactive, reliable, and dedicated to maintaining the confidentiality of financial information while continually seeking to improve processes and enhance efficiency. You are customer service oriented and committed to working with vendors and internal budget managers.\n",
  "hostedUrl": "https://jobs.lever.co/gohighlevel/cdaaed4d-638d-4348-b2ca-f539a5f0fef5",
  "applyUrl": "https://jobs.lever.co/gohighlevel/cdaaed4d-638d-4348-b2ca-f539a5f0fef5/apply"
}
```
</details>

<BR>

# Workday
**Pattern**: `https://{tenant}.{wd_host}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs`
* List is a POST with JSON body `{"appliedFacets":{},"limit":20,"offset":0,"searchText":""}` — returns `total` + `jobPostings[]` (paginate with `offset`, limit caps at 20).
* Detail: GET `/wday/cxs/{tenant}/{site}{externalPath}` — `externalPath` comes from each list item.
* Hosted posting page = `https://{tenant}.{wd_host}.myworkdayjobs.com/{site}{externalPath}` (returned as `externalUrl` in the detail).
* Tenant, host and site are in the careers URL `{tenant}.{wd_host}.myworkdayjobs.com/{site}` (wd_host is wd1/wd3/wd5/wd12...).


<details><summary>Sample Outputs</summary>

POST /wday/cxs/nvidia/NVIDIAExternalCareerSite/jobs (limit=1)
---
```json
{
  "total": 2000,
  "jobPostings": [
    {
      "title": "Senior Customer Program Manager – AI Platform",
      "externalPath": "/job/US-CA-Santa-Clara/Senior-Customer-Program-Manager---AI-Platform_JR2021831-1",
      "locationsText": "US, CA, Santa Clara",
      "postedOn": "Posted Today",
      "bulletFields": [
        "JR2021831"
      ]
    }
  ],
  "facets": [
    {
      "facetParameter": "jobFamilyGroup",
      "descriptor": "Job Category",
      "values": [
        {
          "descriptor": "Engineering",
          "id": "0c40f6bd1d8f10ae43ffaefd46dc7e78",
          "count": 1828
        },
        {
          "descriptor": "Sales",
          "id": "0c40f6bd1d8f10ae43ffcac5bbec7e90",
          "count": 276
        }
      ]
    },
    {
      "facetParameter": "workerSubType",
      "descriptor": "Job Type",
      "values": [
        {
          "descriptor": "Regular Employee",
          "id": "0c40f6bd1d8f10adf6dae161b1844a15",
          "count": 2300
        },
        {
          "descriptor": "Management",
          "id": "0c40f6bd1d8f10adf6dae2cd57444a16",
          "count": 193
        }
      ]
    }
  ],
  "userAuthenticated": false
}
```
GET /wday/cxs/nvidia/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Customer-Program-Manager---AI-Platform_JR2021831-1
---
```json
{
  "jobPostingInfo": {
    "id": "121c333bf790102ee5a50a4c745f0000",
    "title": "Senior Customer Program Manager – AI Platform",
    "jobDescription": "<p>We are seeking a Senior Customer Program Manager to support the planning and execution of technical and operational programs for key partners and maintain strong customer relationships. As a key member of the Customer Program Management Team, this role will drive collaboration with major enterprise customers, cloud service providers, and Tier-1 partners. The ideal candidate will bring strong technical program management expertise, a solid engineering background, and the ability to thrive in fast-paced environments while ensuring successful outcomes for both internal teams and external collaborators.</p><p></p><p><b>What You’ll Be Doing:</b></p><ul><li><p>Define and manage program schedules, deliverables, and turning points aligned with customers and partners roadmaps and requirements.</p></li><li><p>Translate customer requirements into actionable tasks for all functional teams, driving timely issue resolution across engineering, quality, logistics, and sales.</p></li><li><p>Provide horizontal leadership to drive cross-functional program execution, managing high priority issues/concerns, and coordinating globally dispersed teams to ensure clear alignment on objectives.</p></li><li><p>Act as the primary customer interface, facilitating program kick-offs, technical discussions, design reviews, issues/bug tracking and resolution, customer qualification/validation efforts, and status updates throughout the product lifecycle.</p></li><li><p>Drive program execution from design through production deployment, ensuring on-time delivery, quality, and customer acceptance.</p></li><li><p>Provide ongoing post-deployment sustaining support, serving as case manager for high-priority concerns</p></li><li><p>Proactively communicate program health, risks, and key issues to both customers, internal partners, and leadership (Hardware, Software, Operations, Business, Quality, Sales, and Product Teams), ensuring transparency and alignment.</p></li><li><p>Define the strategy and execution plans for successfully integrating complex AI model tech stacks in customer environments, from evaluation/proof-of-concept to design, development, qualification, and validation to production launch</p></li></ul><p></p><p><b>What We Need to See:</b></p><ul><li><p>Bachelor’s or master’s degree in computer science, Engineering, or a related field, or equivalent experience.</p></li><li><p>12&#43; years of program/project management experience in Cloud providers, Automotive, Robotics or Physical AI industries</p></li><li><p>Strong technical background in hardware and/or software product development with experience working closely with cloud and AI solution providers</p></li><li><p>Experience driving AI Systems Orchestration.  Aligning engineering teams to scale large generative architectures while managing massive compute resources, infrastructure bottlenecks, and model release timelines</p></li><li><p>Consistent track record of leading complex, global programs through full development lifecycles into deployment and production</p></li><li><p>Excellent program management, communication, and organizational skills with the ability to prioritize issues and drive cross-functional alignment.</p></li><li><p>Consistent track record to coordinate with global teams and navigate multi-cultural, multinational business environments.</p></li><li><p>Excellent interpersonal skills with the ability to distill complex technical concepts for diverse technical and non-technical audiences, from engineers to executives.</p></li><li><p>Willingness to travel as required (up to 10%), and thrive in fast-paced, dynamic environments</p></li></ul><p></p><p><b>Ways to Stand Out from the Crowd:</b></p><ul><li><p>Experience working with Synthetic data generation pipelines and/or World Foundation Models</p></li><li><p>Experience managing data Lifecycle Management: Overseeing large-scale data curation pipelines, defining automated quality metrics for training data, and mitigating legal, privacy, and bias risks</p></li><li><p>Strong leadership in matrixed collaboration, conflict resolution, and consensus-building.</p></li><li><p>Strong background thriving in dynamic, high-growth environments, with proven adaptability to obscurity, rapid change, and matrixed team structures.</p></li></ul><p></p><p>With competitive salaries and a generous benefits package, we are widely considered to be one of the world’s most desirable employers! We have some of the most forward-thinking and hardworking people in the world working for us and, due to outstanding growth, our best-in-class engineering teams are rapidly growing. If you&#39;re a creative and autonomous person with a real passion for technology, we want to hear from you.</p><p style=\"text-align:inherit\"></p><p style=\"text-align:inherit\"></p>Your base salary will be determined based on your location, experience, and the pay of employees in similar positions. The base salary range is 200,000 USD - 322,000 USD.<div><div><div><div><div><div><div><p></p><p><span>You will also be eligible for equity and <a href=\"https://www.nvidia.com/en-us/benefits/\" target=\"_blank\">benefits</a>.</span></p><p></p></div></div></div></div></div></div></div>Applications for this job will be accepted at least until August 3, 2026.<p></p><p>This posting is for an existing vacancy. </p><p>NVIDIA uses AI tools in its recruiting processes.</p><p></p>NVIDIA is committed to fostering an inclusive work environment and proud to be an equal opportunity employer. As we highly value diversity in our current and future employees, we do not discriminate (including in our hiring and promotion practices) on the basis of race, religion, color, national origin, gender, gender expression, sexual orientation, age, marital status, veteran status, disability status or any other characteristic protected by law.<p style=\"text-align:inherit\"></p><p style=\"text-align:inherit\"></p>",
    "location": "US, CA, Santa Clara",
    "postedOn": "Posted Today",
    "startDate": "2026-07-30",
    "timeType": "Full time",
    "jobReqId": "JR2021831",
    "jobPostingId": "Senior-Customer-Program-Manager---AI-Platform_JR2021831-1",
    "jobPostingSiteId": "NVIDIAExternalCareerSite",
    "country": {
      "descriptor": "United States of America",
      "id": "bc33aa3152ec42d4995f4791a106ed09"
    },
    "canApply": true,
    "posted": true,
    "includeResumeParsing": true,
    "jobRequisitionLocation": {
      "descriptor": "US, CA, Santa Clara",
      "country": {
        "descriptor": "United States of America",
        "id": "bc33aa3152ec42d4995f4791a106ed09",
        "alpha2Code": "US"
      }
    },
    "externalUrl": "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/US-CA-Santa-Clara/Senior-Customer-Program-Manager---AI-Platform_JR2021831-1",
    "questionnaireId": "2f2764b3a82910064296831695150000"
  },
  "hiringOrganization": {
    "name": "2100 NVIDIA USA",
    "url": ""
  },
  "similarJobs": [],
  "userAuthenticated": false
}
```
</details>

<BR>

# SmartRecruiters
**Pattern**: `https://api.smartrecruiters.com/v1/companies/{company}/postings`
* Returns `{offset, limit, totalFound, content[]}` — content items are summaries without description; paginate with `?limit=100&offset=N`.
* Detail: `/v1/companies/{company}/postings/{posting_id}` — adds `jobAd.sections` (HTML blocks: companyDescription, jobDescription, qualifications, additionalInformation) plus `postingUrl`, `applyUrl`, `referralUrl`.
* Company identifier is in the careers URL `careers.smartrecruiters.com/{company}` or `jobs.smartrecruiters.com/{company}/{posting_id}`.

<details><summary>Sample Outputs</summary>

GET /v1/companies/Visa/postings?limit=1
---
```json
{
  "offset": 0,
  "limit": 1,
  "totalFound": 2,
  "content": [
    {
      "id": "744000133907678",
      "name": "Sr. Manager",
      "uuid": "267d47c7-29af-4c8f-a19d-c112895329df",
      "jobAdId": "2132a049-8eb1-4bef-bc4c-13b6d21c05e3",
      "defaultJobAd": true,
      "refNumber": "REF97395W",
      "company": {
        "identifier": "Visa",
        "name": "Visa"
      },
      "releasedDate": "2026-06-24T10:00:11.853Z",
      "location": {
        "city": "Austin",
        "region": "TX",
        "country": "us",
        "remote": false,
        "hybrid": false,
        "latitude": "30.267153",
        "longitude": "-97.7430608",
        "fullLocation": "Austin, TX, United States"
      },
      "industry": {
        "id": "it_and_services",
        "label": "Information Technology And Services"
      },
      "department": {
        "id": "868639",
        "label": "Software Development/Engineering"
      },
      "function": {
        "id": "engineering",
        "label": "Engineering"
      },
      "typeOfEmployment": {
        "id": "permanent",
        "label": "Full-time"
      },
      "experienceLevel": {
        "id": "mid_senior_level",
        "label": "Mid-Senior Level"
      },
      "customField": [
        {
          "fieldId": "58b7e4d1e4b0885c92cd9895",
          "fieldLabel": "Department",
          "valueId": "868639",
          "valueLabel": "Software Development/Engineering"
        },
        {
          "fieldId": "COUNTRY",
          "fieldLabel": "Country/Region",
          "valueId": "us",
          "valueLabel": "United States"
        }
      ],
      "visibility": "PUBLIC",
      "ref": "https://api.smartrecruiters.com/v1/companies/Visa/postings/744000133907678",
      "language": {
        "code": "en",
        "label": "English",
        "labelNative": "English (US)"
      }
    }
  ]
}
```
GET /v1/companies/Visa/postings/744000133907678
---
```json
{
  "id": "744000133907678",
  "name": "Sr. Manager",
  "uuid": "267d47c7-29af-4c8f-a19d-c112895329df",
  "jobId": "98046ec5-df31-450d-88c2-ec312f81c151",
  "jobAdId": "2132a049-8eb1-4bef-bc4c-13b6d21c05e3",
  "defaultJobAd": true,
  "refNumber": "REF97395W",
  "company": {
    "name": "Visa",
    "identifier": "Visa"
  },
  "location": {
    "city": "Austin",
    "region": "TX",
    "country": "us",
    "remote": false,
    "hybrid": false,
    "latitude": "30.267153",
    "longitude": "-97.7430608",
    "fullLocation": "Austin, TX, United States"
  },
  "customField": [
    {
      "fieldId": "COUNTRY",
      "fieldLabel": "Country/Region",
      "valueId": "us",
      "valueLabel": "United States"
    },
    {
      "fieldId": "5bd89e45b1f60b7aea922c5b",
      "fieldLabel": "Visa Inc. or Visa in Europe job?",
      "valueId": "3a568048-d1c9-4741-be56-36b4f3addff7",
      "valueLabel": "Visa Inc."
    }
  ],
  "releasedDate": "2026-06-24T10:00:11.853Z",
  "creator": {
    "name": "",
    "avatarUrl": ""
  },
  "postingUrl": "https://jobs.smartrecruiters.com/Visa/744000133907678-sr-manager",
  "applyUrl": "https://jobs.smartrecruiters.com/Visa/744000133907678-sr-manager?oga=true",
  "referralUrl": "https://jobs.smartrecruiters.com/external-referrals/company/Visa/publication/267d47c7-29af-4c8f-a19d-c112895329df?dcr_ci=Visa",
  "jobAd": {
    "sections": {
      "companyDescription": {
        "title": "Company Description",
        "text": ""
      },
      "jobDescription": {
        "title": "Job Description",
        "text": ""
      },
      "qualifications": {
        "title": "Qualifications",
        "text": ""
      },
      "additionalInformation": {
        "title": "Additional Information",
        "text": "<p>All your information will be kept confidential according to EEO guidelines.</p>"
      }
    }
  },
  "active": true,
  "visibility": "PUBLIC",
  "industry": {
    "id": "it_and_services",
    "label": "Information Technology And Services"
  },
  "function": {
    "id": "engineering",
    "label": "Engineering"
  },
  "department": {
    "id": 868639,
    "label": "Software Development/Engineering"
  },
  "experienceLevel": {
    "id": "mid_senior_level",
    "label": "Mid-Senior Level"
  },
  "typeOfEmployment": {
    "id": "permanent",
    "label": "Full-time"
  },
  "language": {
    "code": "en",
    "label": "English",
    "labelNative": "English (US)"
  }
}
```
</details>



<BR>

# Workable
**Pattern**: `https://apply.workable.com/api/v1/widget/accounts/{account}?details=true`
* Response is `{name, description, jobs[]}` — every published job in one call; `details=true` adds full HTML `description` per job (otherwise summary fields only).
* url/shortlink is the hosted posting; application_url is the application page.
* Account name is in the careers URL `apply.workable.com/{account}` (or `{account}.workable.com`).

<details><summary>Sample Outputs</summary>

GET /api/v1/widget/accounts/huggingface (one element of jobs[], details=false)
---
```
{
  "title": "Low-level Senior Software Engineer, Xet Storage - EMEA Remote",
  "shortcode": "F4C096B22E",
  "code": "",
  "employment_type": "Full-time",
  "telecommuting": true,
  "department": "Product",
  "url": "https://apply.workable.com/j/F4C096B22E",
  "shortlink": "https://apply.workable.com/j/F4C096B22E",
  "application_url": "https://apply.workable.com/j/F4C096B22E/apply",
  "published_on": "2026-07-30",
  "created_at": "2026-07-30",
  "country": "France",
  "city": "Paris",
  "state": "Île-de-France",
  "education": "Master's Degree",
  "experience": "Mid-Senior level",
  "function": "Engineering",
  "industry": "Computer Software",
  "locations": [
    {
      "country": "France",
      "countryCode": "FR",
      "city": "Paris",
      "region": "Île-de-France",
      "hidden": true
    }
  ]
}
```
GET /api/v1/widget/accounts/huggingface?details=true (jobs[] trimmed to 2)
---
```json
{
  "name": "Hugging Face",
  "description": "Here at Hugging Face, we're on a journey to advance and democratize machine learning for everyone. Along the way, we contribute to the development of technology for the better. Over five thousand companies are using our technology in production, including leading AI organizations such as Google, Elastic, Salesforce, Algolia, and Grammarly.",
  "jobs": [
    {
      "title": "Low-level Senior Software Engineer, Xet Storage - EMEA Remote",
      "shortcode": "F4C096B22E",
      "code": "",
      "employment_type": "Full-time",
      "telecommuting": true,
      "department": "Product",
      "url": "https://apply.workable.com/j/F4C096B22E",
      "shortlink": "https://apply.workable.com/j/F4C096B22E",
      "application_url": "https://apply.workable.com/j/F4C096B22E/apply",
      "published_on": "2026-07-30",
      "created_at": "2026-07-30",
      "country": "France",
      "city": "Paris",
      "state": "Île-de-France",
      "education": "Master's Degree",
      "experience": "Mid-Senior level",
      "function": "Engineering",
      "industry": "Computer Software",
      "locations": [
        {
          "country": "France",
          "countryCode": "FR",
          "city": "Paris",
          "region": "Île-de-France",
          "hidden": true
        }
      ],
      "description": "<p></p><p>At Hugging Face, we're on a journey to democratize good AI. We are building the fastest growing platform for AI builders with over 11 million users who collectively shared over 4 million models, 1 million datasets &amp; 1.5 million Gradio apps. Our open-source libraries have more than 700,000 stars on Github.</p><p></p><p><strong>About the Role</strong></p><p>Roles at Hugging Face are very fluid and dynamic -- we're looking for someone who is comfortable taking on different challenges that evolve over time. This role sits on the <a href=\"https://huggingface.co/docs/hub/xet/index\" rel=\"nofollow noreferrer noopener\" class=\"external\">Xet Storage</a> team, the group responsible for the storage system behind all of Hugging Face. Today we store over 200PB (and growing rapidly!) of the world's most important ML &amp; AI assets. Xet is the underlying storage architecture for the entire Hugging Face platform and community -- from the largest model repositories to the datasets and Spaces that millions of people build on every day.</p><p></p><p>In this role, you'll work across two closely connected surfaces. You'll contribute to <a href=\"https://github.com/huggingface/xet-core\" rel=\"nofollow noreferrer noopener\" class=\"external\">xet-core</a>, our open-source project written in Rust that powers <a href=\"https://pypi.org/project/hf-xet/\" rel=\"nofollow noreferrer noopener\" class=\"external\">hf-xet</a> -- the Python library underpinning the Hugging Face Hub client and the wider ecosystem of open-source tools the community relies on. And you'll design, build, and operate meaningful and challenging features in the Xet Storage backend, contributing to the broader Infrastructure organization at Hugging Face. We're a small team building and operating incredible things at enormous scale, in a high-trust, low-process, async, and remote environment -- and we lean heavily on the latest AI tools to move fast.</p><p></p><p>If you love writing low-level, high-performance code and are energized by the challenge of large-scale, scalable services, this is the team for you. This usually means proven experience building and operating production systems software, but we consider every applicant on an individual basis.</p><p><strong>Requirements</strong></p><p></p><p><strong>What we're looking for</strong></p><ul><li>8+ years building and scaling distributed systems, storage, or networking infrastructure.</li><li>Proficiency in a low-level systems language, with Rust strongly preferred (we also work in Python, Typescript, Go, and C++ across the stack).</li><li>A track record of working independently in a high-trust, low-process environment -- you're comfortable with ambiguity and take ownership end to end.</li><li>Comfort operating in a fast-moving, async, and fully remote environment.</li><li>A passion for building simple, robust, and scalable systems relied on by engineering and science teams around the world.</li></ul><p><strong>Bonus points if you have</strong></p><ul><li>Experience designing efficient, high-performance, fault-tolerant data storage and retrieval systems.</li><li>Experience operating production systems -- monitoring, alerting, and distributed debugging and recovery.</li><li>Familiarity with git internals, cloud infrastructure (AWS, Azure, GCP, Kubernetes), databases (relational and non-relational), or networking.</li></ul><p></p><p><strong>About You</strong></p><p>If you love open-source, are excited by the intersection of low-level performance work and large-scale services, and want your code to sit at the foundation of the world's largest platform for AI builders, then we can't wait to see your application!</p><p></p><p>If you're interested in joining us, but don't tick every box above, we still encourage you to apply! We're building a diverse team whose skills, experiences, and backgrounds complement one another. We're happy to consider where you might be able to make the biggest impact.</p><p></p><p><strong>One more thing</strong></p><p>At Hugging Face we believe great AI shouldn't require a massive cluster, we build for everyone, especially the GPU-poor. And because we read every application, here's a small sign that you read this one too: start your answer to the first application question with the words “<strong>GPU-poor and proud&nbsp;</strong>🤗”. No trick, no catch, it just tells us a real person is on the other side.</p><p><strong>Benefits</strong></p><p><strong>More about Hugging Face</strong></p><p><strong>We are actively working to build a culture that values diversity, equity, and inclusivity</strong>. We are intentionally building a workplace where people feel respected and supported—regardless of who you are or where you come from. We believe this is foundational to building a great company and community. Hugging Face is an equal opportunity employer and we do not discriminate on the basis of race, religion, color, national origin, gender, sexual orientation, age, marital status, veteran status, or disability status.</p><p><strong>We value development.</strong>&nbsp;You will work with some of the smartest people in our industry. We are an organization that has a bias for impact and is always challenging ourselves to continuously grow. We provide all employees with reimbursement for relevant conferences, training, and education.</p><p><strong>We care about your well-being.</strong>&nbsp;We offer flexible working hours and remote options. We offer health, dental, and vision benefits for employees and their dependents. We also offer parental leave and flexible paid time off.</p><p><strong>We support our employees wherever they are</strong>. While we have office spaces in NYC and Paris, we're very distributed and all remote employees have the opportunity to visit our offices. If needed, we'll also outfit your workstation to ensure you succeed.</p><p><strong>We want our teammates to be shareholders</strong>. All employees have company equity as part of their compensation package. If we succeed in becoming a category-defining platform in machine learning and artificial intelligence, everyone enjoys the upside.</p><p><strong>We support the community</strong>. We believe major scientific advancements are the result of collaboration across the field. Join a community supporting the ML/AI community.</p>"
    },
    {
      "title": "Low-level Senior Software Engineer, Xet Storage - US Remote",
      "shortcode": "002470F128",
      "code": "",
      "employment_type": "Full-time",
      "telecommuting": true,
      "department": "Product",
      "url": "https://apply.workable.com/j/002470F128",
      "shortlink": "https://apply.workable.com/j/002470F128",
      "application_url": "https://apply.workable.com/j/002470F128/apply",
      "published_on": "2026-07-30",
      "created_at": "2026-07-30",
      "country": "United States",
      "city": "New York",
      "state": "New York",
      "education": "Master's Degree",
      "experience": "Mid-Senior level",
      "function": "Engineering",
      "industry": "Computer Software",
      "locations": [
        {
          "country": "United States",
          "countryCode": "US",
          "city": "New York",
          "region": "New York",
          "hidden": true
        }
      ],
      "description": "<p></p><p>At Hugging Face, we're on a journey to democratize good AI. We are building the fastest growing platform for AI builders with over 11 million users who collectively shared over 4 million models, 1 million datasets &amp; 1.5 million Gradio apps. Our open-source libraries have more than 700,000 stars on Github.</p><p></p><p><strong>About the Role</strong></p><p>Roles at Hugging Face are very fluid and dynamic -- we're looking for someone who is comfortable taking on different challenges that evolve over time. This role sits on the <a href=\"https://huggingface.co/docs/hub/xet/index\" rel=\"nofollow noreferrer noopener\" class=\"external\">Xet Storage</a> team, the group responsible for the storage system behind all of Hugging Face. Today we store over 200PB (and growing rapidly!) of the world's most important ML &amp; AI assets. Xet is the underlying storage architecture for the entire Hugging Face platform and community -- from the largest model repositories to the datasets and Spaces that millions of people build on every day.</p><p></p><p>In this role, you'll work across two closely connected surfaces. You'll contribute to <a href=\"https://github.com/huggingface/xet-core\" rel=\"nofollow noreferrer noopener\" class=\"external\">xet-core</a>, our open-source project written in Rust that powers <a href=\"https://pypi.org/project/hf-xet/\" rel=\"nofollow noreferrer noopener\" class=\"external\">hf-xet</a> -- the Python library underpinning the Hugging Face Hub client and the wider ecosystem of open-source tools the community relies on. And you'll design, build, and operate meaningful and challenging features in the Xet Storage backend, contributing to the broader Infrastructure organization at Hugging Face. We're a small team building and operating incredible things at enormous scale, in a high-trust, low-process, async, and remote environment -- and we lean heavily on the latest AI tools to move fast.</p><p></p><p>If you love writing low-level, high-performance code and are energized by the challenge of large-scale, scalable services, this is the team for you. This usually means proven experience building and operating production systems software, but we consider every applicant on an individual basis.</p><p><strong>Requirements</strong></p><p></p><p><strong>What we're looking for</strong></p><ul><li>8+ years building and scaling distributed systems, storage, or networking infrastructure.</li><li>Proficiency in a low-level systems language, with Rust strongly preferred (we also work in Python, Typescript, Go, and C++ across the stack).</li><li>A track record of working independently in a high-trust, low-process environment -- you're comfortable with ambiguity and take ownership end to end.</li><li>Comfort operating in a fast-moving, async, and fully remote environment.</li><li>A passion for building simple, robust, and scalable systems relied on by engineering and science teams around the world.</li></ul><p><strong>Bonus points if you have</strong></p><ul><li>Experience designing efficient, high-performance, fault-tolerant data storage and retrieval systems.</li><li>Experience operating production systems -- monitoring, alerting, and distributed debugging and recovery.</li><li>Familiarity with git internals, cloud infrastructure (AWS, Azure, GCP, Kubernetes), databases (relational and non-relational), or networking.</li></ul><p></p><p><strong>About You</strong></p><p>If you love open-source, are excited by the intersection of low-level performance work and large-scale services, and want your code to sit at the foundation of the world's largest platform for AI builders, then we can't wait to see your application!</p><p></p><p>If you're interested in joining us, but don't tick every box above, we still encourage you to apply! We're building a diverse team whose skills, experiences, and backgrounds complement one another. We're happy to consider where you might be able to make the biggest impact.</p><p></p><p><strong>One more thing</strong></p><p>At Hugging Face we believe great AI shouldn't require a massive cluster, we build for everyone, especially the GPU-poor. And because we read every application, here's a small sign that you read this one too: start your answer to the first application question with the words “<strong>GPU-poor and proud&nbsp;</strong>🤗”. No trick, no catch, it just tells us a real person is on the other side.</p><p><strong>Benefits</strong></p><p><strong>More about Hugging Face</strong></p><p><strong>We are actively working to build a culture that values diversity, equity, and inclusivity</strong>. We are intentionally building a workplace where people feel respected and supported—regardless of who you are or where you come from. We believe this is foundational to building a great company and community. Hugging Face is an equal opportunity employer and we do not discriminate on the basis of race, religion, color, national origin, gender, sexual orientation, age, marital status, veteran status, or disability status.</p><p><strong>We value development.</strong>&nbsp;You will work with some of the smartest people in our industry. We are an organization that has a bias for impact and is always challenging ourselves to continuously grow. We provide all employees with reimbursement for relevant conferences, training, and education.</p><p><strong>We care about your well-being.</strong>&nbsp;We offer flexible working hours and remote options. We offer health, dental, and vision benefits for employees and their dependents. We also offer parental leave and flexible paid time off.</p><p><strong>We support our employees wherever they are</strong>. While we have office spaces in NYC and Paris, we're very distributed and all remote employees have the opportunity to visit our offices. If needed, we'll also outfit your workstation to ensure you succeed.</p><p><strong>We want our teammates to be shareholders</strong>. All employees have company equity as part of their compensation package. If we succeed in becoming a category-defining platform in machine learning and artificial intelligence, everyone enjoys the upside.</p><p><strong>We support the community</strong>. We believe major scientific advancements are the result of collaboration across the field. Join a community supporting the ML/AI community.</p>"
    }
  ]
}
```
</details>




# Rippling
**Pattern**: `https://api.rippling.com/platform/api/ats/v1/board/{board}/jobs`
* Returns a bare array of job summaries (no description).
* Detail: `/platform/api/ats/v1/board/{board}/jobs/{uuid}` — adds HTML `description`, `workLocations[]`, `employmentType`, `payRangeDetails`, embedded `jsonLd`, etc.
* Board name is in the careers URL `ats.rippling.com/{board}`.
* Schema changed at some point: older captures (first block below) have `id`, `locations[]`, `language`; the current API returns `uuid`, `department{id,label}`, `workLocation`.

<details><summary>Sample Outputs</summary>

GET /platform/api/ats/v1/board/joinroot/jobs (older schema — one element)
---
```
{
  "id": "45807387-46bf-4bfa-aa72-79363740ea21",
  "name": "Business Development Operations Analyst",
  "url": "https://ats.rippling.com/joinroot/jobs/45807387-46bf-4bfa-aa72-79363740ea21",
  "department": {
    "name": "BD"
  },
  "locations": [
    {
      "name": "Remote (United States)",
      "country": "United States",
      "countryCode": "US",
      "state": null,
      "stateCode": null,
      "city": null,
      "workplaceType": "REMOTE"
    }
  ],
  "language": "en-US"
}
```
GET /platform/api/ats/v1/board/joinroot/jobs (current schema, trimmed to 2)
---
```json
[
  {
    "uuid": "45807387-46bf-4bfa-aa72-79363740ea21",
    "name": "Business Development Operations Analyst",
    "department": {
      "id": "BD",
      "label": "BD"
    },
    "url": "https://ats.rippling.com/joinroot/jobs/45807387-46bf-4bfa-aa72-79363740ea21",
    "workLocation": {
      "label": "Remote (United States)",
      "id": "Remote (United States)"
    }
  },
  {
    "uuid": "50854581-b199-402c-8545-2b426b32c20e",
    "name": "Business Development Representative, IA",
    "department": {
      "id": "BD",
      "label": "BD"
    },
    "url": "https://ats.rippling.com/joinroot/jobs/50854581-b199-402c-8545-2b426b32c20e",
    "workLocation": {
      "label": "Remote (United States)",
      "id": "Remote (United States)"
    }
  }
]
```
GET /platform/api/ats/v1/board/joinroot/jobs/45807387-46bf-4bfa-aa72-79363740ea21
---
```json
{
  "uuid": "45807387-46bf-4bfa-aa72-79363740ea21",
  "name": "Business Development Operations Analyst",
  "description": {
    "company": "<meta><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;text-align:justify;\"><span style=\"color:rgb(29,28,29);background-color:rgb(255,255,255);white-space:pre-wrap;\">Root was founded on the belief that car insurance is broken, and we set out to change it. We’re harnessing the power of technology to revolutionize this archaic, complicated industry. Using machine learning and mobile telematic platforms, we’ve built one of the most innovative insurtech companies in the world. </span></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;text-align:justify;\"><br></p>",
    "role": "<meta><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><b><strong style=\"white-space:pre-wrap;\">The Opportunity</strong></b></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.38;letter-spacing:0.25px;margin:3pt 0px;padding:0px;\"><span style=\"color:rgb(0,0,0);font-size:11pt;white-space:pre-wrap;\">The Business Development Operations Analyst will support Root's Business Development team by helping coordinate the processes, systems, and workflows that power our partnerships and independent agents organization. Working closely with Business Development leaders and cross-functional teams, you'll assist with partner onboarding, contracting, commission administration, CRM management, reporting, and other key operational functions.</span></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><br></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.38;letter-spacing:0.25px;margin:3pt 0px;padding:0px;\"><span style=\"color:rgb(0,0,0);font-size:11pt;white-space:pre-wrap;\">Success in this role means ensuring operational processes run smoothly, maintaining accurate data and systems, and helping the Business Development team execute efficiently.</span></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><br></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;text-align:left;\"><b><strong style=\"white-space:pre-wrap;\">Salary Range</strong></b><span style=\"white-space:pre-wrap;\">: &nbsp;$73,200 - $88,400 (</span><span style=\"color:rgb(32,32,34);font-size:11pt;white-space:pre-wrap;\">Eligible for Competitive Bonus &amp; Equity Offering)</span><br><br><span style=\"white-space:pre-wrap;\">Root is a “work where it works best” company. Meaning we will support you working in whatever location that works best for you across the US. We will continue to have our headquarters in Columbus, Ohio.</span></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><br></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;text-align:left;\"><b><strong style=\"white-space:pre-wrap;\">How You Will Make an Impact</strong></b></p><ul data-pattern=\"discCircleSquare\" data-depth=\"1\" style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;margin:8px 0px;line-height:1.6;padding:0px 0px 0px 32px;list-style-type:disc;\"><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Manage day-to-day operational activities for the Business Development team, ensuring partner requests and internal workflows are completed accurately and on time.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Coordinate partner onboarding activities across Business Development, Legal, Finance, Product, Engineering, and Operations, escalating risks or blockers when needed.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Manage contracting workflows by tracking agreement status, maintaining records, and coordinating internal reviews and approvals.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Support commission and incentive payment processes, including calculations, approvals, reconciliation, issue resolution, and reporting.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Maintain accurate partner records, account information, pipeline data, and operational reporting within Monday.com CRM and other business systems.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Review existing processes, identify opportunities for improvement, and develop recommendations for the Sales Operations leader.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Implement approved process changes, automations, and workflows that reduce manual work and improve efficiency.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Develop and maintain process documentation, operating procedures, and resources that help teams work consistently and effectively.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Investigate operational issues, propose next steps, and coordinate with stakeholders through resolution.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Partner with stakeholders across the business to gather requirements, support projects, and keep key initiatives moving forward.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Provide operational support for Business Development programs, initiatives, and partner relationships.</span></li><li style=\"font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Help build scalable operational practices as Root’s partnership and independent agent organizations continue to grow.</span></li></ul><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;text-align:left;\"><br><b><strong style=\"white-space:pre-wrap;\">What You Will Need to Succeed</strong></b></p><ul data-pattern=\"discCircleSquare\" data-depth=\"1\" style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;margin:8px 0px;line-height:1.6;padding:0px 0px 0px 32px;list-style-type:disc;\"><li style=\"color:rgb(0,0,0);--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">4+ years of experience in business operations, sales operations, partnership operations, project coordination, or a related role.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Demonstrated ability to independently manage established processes and operational workstreams.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Strong judgment and problem-solving skills, with the ability to assess issues, develop recommendations, and seek alignment before implementing material changes.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Strong organizational skills and attention to detail, with the ability to prioritize competing needs and follow through on commitments.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Excellent written and verbal communication skills, with the ability to clearly communicate findings, recommendations, and risks.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Experience working with CRM platforms, with Monday.com experience preferred, and maintaining data quality and system accuracy.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Experience supporting contracting, procurement, commission, or vendor management processes.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Strong spreadsheet skills and proficiency with business productivity and reporting tools.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">A proactive mindset with the ability to identify opportunities, take ownership of execution, and escalate decisions appropriately.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Ability to navigate ambiguity, make progress with limited day-to-day direction, and recognize when leadership input is required.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">Ability to quickly learn new systems, processes, and business concepts.</span></li><li style=\"--listitem-marker-color:#000000;font-size:11pt;margin:3px 0px;letter-spacing:0.25px;line-height:1.6;\"><span style=\"color:rgb(0,0,0);white-space:pre-wrap;\">A collaborative and adaptable approach, with a willingness to support a range of operational and cross-functional priorities.</span></li></ul><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;text-align:left;\"><br><br><span style=\"color:rgb(29,28,29);background-color:rgb(255,255,255);white-space:pre-wrap;\">As part of Root's interview process, we kindly ask that all candidates be on camera for virtual interviews. This helps us create a more personal and engaging experience for both you and our interviewers. Being on camera is a standard requirement for our process and part of how we assess fit and communication style, so we do require it to move forward with any applicant's candidacy. If you have any concerns, feel free to let us know once you are contacted. We’re happy to talk it through.</span></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><span style=\"color:rgb(29,28,29);white-space:pre-wrap;\">Please see our Privacy Notice available&nbsp;</span><a href=\"http://inc.joinroot.com/california-job-applicant-privacy-notice/\" target=\"_blank\" class=\"css-173makr-linkStyle\" style=\"color:rgb(30,74,169);cursor:pointer;\"><span style=\"color:rgb(29,28,29);white-space:pre-wrap;\">HERE</span></a><span style=\"color:rgb(29,28,29);white-space:pre-wrap;\">&nbsp;for more information on how we process your personal data.</span></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><br></p><p style=\"font-family:&quot;Basel Grotesk&quot;,Arial,sans-serif;font-size:11pt;font-weight:400;line-height:1.6;letter-spacing:0.25px;margin:4px 0px;padding:0px;\"><span style=\"color:rgb(29,28,29);white-space:pre-wrap;\">Consistent with the Americans with Disabilities Act (ADA) and the Civil Rights Act of 1964, it is the policy of Root to provide reasonable accommodation when requested by a qualified applicant or candidate with a disability, unless such accommodation would cause an undue hardship for Root. The policy regarding requests for reasonable accommodation applies to all aspects of the hiring process. If reasonable accommodation is needed, please contact recruiting@joinroot.com.</span></p>"
  },
  "workLocations": [
    "Remote (United States)"
  ],
  "department": {
    "name": "BD",
    "base_department": "BD",
    "department_tree": [
      "BD"
    ]
  },
  "employmentType": {
    "label": "SALARIED_FT",
    "id": "Salaried, full-time"
  },
  "createdOn": "2026-07-30T06:21:47.810000-07:00",
  "activeJobApplication": {
    "additionalQuestions": [
      {
        "form": {
          "questions": [
            {
              "uniqueKey": "a411a765-d4fc-4f76-84a6-751656864f46",
              "title": "Are you legally permitted to work in the country where this job is located?",
              "description": "",
              "tags": [],
              "dataType": "select",
              "questionType": "KNOCKOUT",
              "isRequired": true,
              "allowComments": false,
              "canEdit": false,
              "isPrivate": false,
              "strChoices": [
                "Yes",
                "No"
              ],
              "intChoices": [],
              "isMultiSelectEnabled": true,
              "isOtherEnabled": false
            },
            {
              "uniqueKey": "bd463b6e-048c-4bd4-83d2-30adbe106971",
              "title": "If hired, can you provide proof of eligibility?",
              "tags": [],
              "dataType": "select",
              "questionType": "KNOCKOUT",
              "isRequired": true,
              "allowComments": false,
              "canEdit": false,
              "isPrivate": false,
              "strChoices": [
                "Yes",
                "No"
              ],
              "intChoices": [],
              "isMultiSelectEnabled": true,
              "isOtherEnabled": false
            }
          ],
          "deletedQuestions": [
            {
              "uniqueKey": "cdf26e09-2528-4995-8943-ec447a5c0d67",
              "title": "Do you have a personal line insurance license?",
              "tags": [],
              "dataType": "enum",
              "questionType": "SINGLE_SELECT_RADIO",
              "isRequired": true,
              "allowComments": false,
              "canEdit": false,
              "isPrivate": false,
              "strChoices": [
                "Yes",
                "No"
              ],
              "intChoices": [],
              "isMultiSelectEnabled": false,
              "isOtherEnabled": false
            }
          ],
          "sections": [],
          "deletedSections": [],
          "skipLogic": []
        },
        "name": "Standard Questionnaire w/Knock Out Question",
        "id": "6a6b4fecbf7ac43ba50bf4a2"
      }
    ],
    "basicQuestions": [
      {
        "oid": "first_name",
        "fieldType": "SHORT_ANSWER",
        "title": "First name",
        "required": true
      },
      {
        "oid": "last_name",
        "fieldType": "SHORT_ANSWER",
        "title": "Last name",
        "required": true
      }
    ]
  },
  "eeocQuestionnaireEnabled": true,
  "eeocQuestionnaireEnabledForJobPost": true,
  "url": "https://ats.rippling.com/joinroot/jobs/45807387-46bf-4bfa-aa72-79363740ea21",
  "board": {
    "boardType": "RIPPLING",
    "slug": "joinroot",
    "logo": {
      "name": "Root_App_Icon.jpg",
      "url": "https://prod-images.rippling.com/0602fc5a80aaa0dac35d48883af8e3d856192152.jpeg?Expires=1785537483&Signature=zuVS~fX4M4moRxVyavY1EjxlH4utEZM4om6z1zm4HkL~Hi-wtrllB8ffsIcO43uIxklkQ4e1ed-i8JyMdJr5YZjacoZConHBt0b~NR9yOo~mrLLUFFTwyldFEJzRXovo-5pe1YE6Hl9cZ6eMB9vYAEMtshCgwL4L73PSU1k-3G5RqxqCN-tTNFqr6qMdEAGowuMy4LpmlRi2Vgq1dyNQamEv81JocD1-X3wUKwOnalAxLg1TjaJR8PmODsZNwT4usoXRxWJM30VUSG0gtUu7ess1HySuAomsSBEu14YK3hRvnnczAEFGCz2NGd4DLCUCYd~xtCtahhwMRt-4h4rKKQ__&Key-Pair-Id=K2Y26R2ZPP26PH",
      "type": "image/jpeg"
    },
    "banner": {
      "name": "",
      "url": null,
      "type": ""
    },
    "title": "Root Insurance",
    "subtitle": null,
    "boardURL": "https://ats.rippling.com/joinroot/jobs",
    "fontType": "ARIAL_SANS_SERIF",
    "linkColor": "#1d1d21",
    "buttonColor": "#ff5715",
    "buttonTextColor": "#fafafa",
    "legalNotice": null,
    "noOpeningsMessage": null,
    "groupJobsByLocation": false,
    "showBoardLogoOnJobPost": true,
    "showCompanyInfoUnderJobPost": false,
    "companyName": "Root Insurance"
  },
  "payRangeDetails": [],
  "applicationConfirmationTemplate": "67bf974a5cc878991295a4df",
  "hasAIEvaluationsEnabled": false,
  "companyName": "Root Insurance",
  "unlistedFromSearch": false,
  "jsonLd": null
}
```
</details>




# Jobvite
**Pattern**: `https://jobs.jobvite.com/{company}/jobs` (**HTML only**)
* The board page is server-rendered HTML; job links have the form `/{company}/job/{job_id}` (e.g. `o16vAfwW`).
* Each job page embeds a schema.org JobPosting in `<script type="application/ld+json">` — that's the extractable structured output (sample below).
* A JSON/XML API exists but is authenticated: `api.jobvite.com/api/v2/job` (API key + secret) and the `app.jobvite.com/CompanyJobs/Xml.aspx?c={token}` feed (per-company token).

<details><summary>Sample Outputs</summary>

JSON-LD extracted from https://jobs.jobvite.com/egnyte/job/o16vAfwW
---
```json
{
  "@context": "http://schema.org",
  "@type": "JobPosting",
  "datePosted": "2026-07-15",
  "description": "<div><div><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;font-weight: bold;\">VP, C</span><span style=\"font-size: 14.0pt;font-weight: bold;\">ORPORATE</span><span style=\"font-size: 14.0pt;font-weight: bold;\"> FP&amp;A</span></span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;font-weight: bold;\">MOUNTAIN VIEW,</span><span style=\"font-size: 14.0pt;font-weight: bold;\"> </span><span style=\"font-size: 14.0pt;font-weight: bold;\">CA – HYBRID,</span><span style=\"font-size: 14.0pt;font-weight: bold;\"> </span><span style=\"font-size: 14.0pt;font-weight: bold;\">3 DAYS PER WEEK IN OFFICE</span></span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">EGNYTE YOUR CAREER. SPARK YOUR PASSION.</span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Egnyte is a place where we spark opportunities for amazing people. We believe that every role has</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">a great impact, and every</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">Egnyter</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">should be respected. When joining Egnyte,</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">you’re</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">not just landing a new career, you become part of a team of</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">Egnyters</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">that are doers, thinkers, and collaborators who embrace and live by our values:</span></span></p><ul style=\"margin: 0.0pt;padding-left: 0.0pt;\"><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Invested Relationships</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Fiscal Prudence</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Candid Conversations</span></li></ul><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">ABOUT EGNYTE</span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Egnyte is the secure multi-cloud platform for content security and governance that enables organizations to better protect and collaborate on their most valuable content. Established in 2008, Egnyte has democratized cloud content security for more than 22,000 organizations, helping customers improve data security,</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">maintain</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">compliance, prevent and detect ransomware threats, and boost employee productivity on any app, any cloud, anywhere. For more information, visit</span><span style=\"font-size: 14.0pt;\"> </span><a style=\"color: rgb(0,0,0);\" href=\"http://www.egnyte.com/\" target=\"_blank\"><span style=\"font-size: 14.0pt;text-decoration: underline;\">www.egnyte.com</span></a><span style=\"font-size: 14.0pt;\">.</span></span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\">Egnyte is seeking a Vice President of Corporate FP&amp;A to serve as a strategic partner to the CFO and executive team driving scalable growth by connecting financial forecasting with deep data insights and operational efficiency. Reporting directly to the CFO, you will lead Egnyte’s FP&amp;A, decision support, and reporting functions, owning the financial narrative that guides how we invest, scale, and win in the market.</span><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\"> </span><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\"> You will not just oversee</span><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\"> </span><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\">numbers,</span><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\"> </span><span style=\"background-color: rgb(255,255,255);font-size: 14.0pt;\">you will shape the financial and operational architecture of a high-growth SaaS company. If you are a self-starter who is fueled by a fast-paced, collaborative environment and can seamlessly translate complex data into actionable executive strategy, we want to “spark” a conversation with you.</span></span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">WHAT YOU’LL DO</span></p><ul style=\"margin: 0.0pt;padding-left: 0.0pt;\"><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Oversee comprehensive planning and for</span><span style=\"font-size: 14.0pt;\">ecasting, including long-range planning (LRP), annual operating planning (AOP), and rolling quarterly forecasts</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Own and manage the master P&amp;L, B/S and C/F model incorporating inputs from various departments and making final judgment in partnership with the CFO</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Drive and lead the financial execution of the company by ensuring that all risks/opportunities are addressed dynamically to hit final goals</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Develop and present sophisticated internal and external reporting packages for executi</span><span style=\"font-size: 14.0pt;\">ve leadership, investors, lenders, and the Board of Directors</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Hire, manage and develop talent to continually build a high performing corporate finance function that scales with the company</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Evaluate the financial viability and strategic fit of new product launches, international expansion, M&amp;A, and business model evolutions</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Deliver deep-dive analysis on revenue drivers, expense optimization, and overall risk/opportunity management</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Contribute to global procurement strategy to optimize vendor management, negotiate high-value software/infrastructure contracts, and drive cost efficiencies</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Implement scalable workflows and compliance guardrails that match Egnyte’s growth trajectory without sacrificing speed</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Oversee the creation of centralized BI dashboards and unified SaaS metrics to drive consistency across all business units</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Core business partner for Corporate, G&amp;A and R&amp;D functions – especially budgeting and headcount</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Optimize vendor management, negotiate high-value software/infrastructure contracts, and drive global cost efficiencies</span></li></ul><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"background-color: rgb(255,255,255);font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">YOUR QUALIFICATIONS:</span></p><ul style=\"margin: 0.0pt;padding-left: 0.0pt;\"><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Bachelor’s degree in Finance, Accounting, Economics, Business, or a related field</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">10+ years of progressive finance experience, including </span><span style=\"font-size: 14.0pt;\">5+ years in an executive leadership role</span><span style=\"font-size: 14.0pt;\"> within a high-growth SaaS or technology company</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Deep understanding of SaaS subscription models, revenue recognition, pipeline mechanics, and operational KPIs</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Expert proficiency with corporate planning platforms, ERPs, CRMs, and Analytics tools (</span><span style=\"font-size: 14.0pt;\">preference with</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">Anaplan, NetSuite, Salesforce, Excel, and Tableau</span><span style=\"font-size: 14.0pt;\">)</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Demonstrated success building, mentoring, and developing high-performing, multi-disciplinary teams</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Exceptional capability to articulate complex financial and data concepts clearly to both technical teams and C-suite executives/Board members</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Willingness to work 3 days per week in our Mountain View, CA office.</span></li></ul><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p><p style=\"margin: 0.0pt 0.0pt 0.0pt 18.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">Preferred Qualifications</span></p><ul style=\"margin: 0.0pt;padding-left: 0.0pt;\"><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 49.4pt;padding-left: 4.6pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">MBA, Master’s degree in a business field, or </span><span style=\"font-size: 14.0pt;\">CPA</span><span style=\"font-size: 14.0pt;\"> designation.</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 49.4pt;padding-left: 4.6pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Background in leading high performing finance teams and delivering financial goals</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 49.4pt;padding-left: 4.6pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Deep understanding of cloud markets, content collaboration, data governance, and security sectors</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 49.4pt;padding-left: 4.6pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Proven ability to own outcomes in fast-paced, ambiguous environments—driving decisions proactively without waiting for direction</span></li></ul><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">COMPENSATION:</span></p><ul style=\"margin: 0.0pt;padding-left: 0.0pt;\"><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Our compensation reflects the cost of labor across multiple U.S. geographic</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">locations, and</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">pay varies based on defined markets. The standard base pay range for this position</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">across the U.S. is $</span><span style=\"font-size: 14.0pt;\">280</span><span style=\"font-size: 14.0pt;\">k</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">- $</span><span style=\"font-size: 14.0pt;\">315</span><span style=\"font-size: 14.0pt;\">k</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">annually.</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">Pay</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">varies by work location</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">and may also be dependent on job-related skills, knowledge,</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">and/or experience.</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">During the interview and/or hiring process, your recruiter can share more information about the compensation package specific to the role and job location.</span></span></li></ul><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;font-weight: bold;\">BENEFITS:</span></p><ul style=\"margin: 0.0pt;padding-left: 0.0pt;\"><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Competitive salaries and comprehensive benefits</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Flexible hours and generous time off (RTO, Responsible Time Off) to help support your work-life balance</span><span style=\"font-size: 14.0pt;\"> </span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Paid holidays and sick time</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">401(k) with company match</span><span style=\"font-size: 14.0pt;\"> </span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Health Savings Account (HSA) with a generous employer contribution and Flexible Spending Account (FSA) options</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Up to 12wks of paid Parental and 10wks Adoption Leave to help you grow your family</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Modern and collaborative offices</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">located</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">in Draper, UT; Raleigh, NC; Mountain View, CA; and Poznan, Poland</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Gym, cell phone, and internet reimbursement</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Free well-being apps such as Spring Health for Guardian are offered, as well access to our Employee Assistance Program (EAP)</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Perks</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">include discounted pet insurance, electronics, theme park tickets, travel, plus more</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Your own Egnyte account with lifetime access</span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">HealthJoy</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">– a benefits navigation app that lets you access your benefits and get answers to your questions all in one place</span></span></li><li style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;margin: 0.0pt 0.0pt 0.0pt 9.9pt;padding-left: 8.1pt;text-indent: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">One Medical virtual care, providing you with healthcare access across the country</span></li></ul><p style=\"margin: 0.0pt 0.0pt 0.0pt 18.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;font-weight: bold;\">Equal Employment Opportunity</span><span style=\"font-size: 14.0pt;\"> </span></span><br /><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\">Egnyte, Inc. is an Equal Opportunity Employer that does not discriminate on the basis of actual or perceived race, color, creed, religion, national origin, ancestry, citizenship status, age, sex or gender (including pregnancy, childbirth, pregnancy-related conditions, and lactation), gender identity or expression (including transgender status), sexual orientation, marital status, military service and veteran status, physical or mental disability, genetic information, or any other characteristic protected by applicable federal, state, or local laws and ordinances. Egnyte, Inc.'s management team is dedicated to this policy with respect to recruitment, hiring, placement, promotion, transfer, training, compensation, benefits, employee activities, access to facilities and programs, and general treatment during employment.</span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">At Egnyte, we embrace our unique differences and thrive on the individuality of our employees, our products, our customers, our investors, and our communities. Our global Egnyte Employee Communities (EECs) support representation and foster connectedness across our varied workplace.</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">Egnyters</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">are encouraged to bring their whole selves to work and to appreciate the many differences that collectively make Egnyte a higher-performing company and</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">a great place</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">to be.</span></span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;\"><span style=\"font-size: 14.0pt;\">Any employees with questions or concerns about equal employment opportunities in the workplace are encouraged to bring these issues to</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">the attention of hr@egnyte.com. Egnyte, Inc. will not allow any form of retaliation against employees who raise issues of equal employment opportunity. If employees feel they have been subjected to any such retaliation, they should contact hr@egnyte.com. To ensure the workplace is free of artificial barriers, violation of this policy including any improper retaliatory conduct will lead to discipline, up to and including discharge. All employees must cooperate with all investigations conducted</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">pursuant to</span><span style=\"font-size: 14.0pt;\"> </span><span style=\"font-size: 14.0pt;\">this policy.</span></span></p><p style=\"margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 5.0pt;\">#LI-DNI</span></p><p style=\"font-size: 14.0pt;line-height: 115.0%;margin: 0.0pt;\"><span style=\"font-family: sans-serif , arial , helvetica;font-size: 14.0pt;\"> </span></p></div></div>",
  "hiringOrganization": "Egnyte",
  "employmentType": "FT - Full Time",
  "industry": "Accounting and Finance",
  "identifier": "o16vAfwW",
  "jobLocation": [
    {
      "@type": "Place",
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "Mountain View",
        "addressRegion": "California",
        "addressCountry": "United States"
      }
    }
  ],
  "title": "VP, Corporate FP&A",
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": {
      "@type": "QuantitativeValue",
      "minValue": "",
      "maxValue": "",
      "unitText": ""
    }
  }
}
```
</details>



# iCIMS
**Pattern**: `https://careers.{company}.com/api/jobs?page=1`
* One call gets everything: each `jobs[].data` is the full posting (HTML `description`, salary, location) — no detail call needed. Paginate with `?page=N` (10 per page).
* Job URL: `jobs[].data.meta_data.canonical_url` (e.g. `https://careers.paychex.com/jobs/44443?lang=en-us`). Apply page: `jobs[].data.apply_url`.
* Legacy `careers-{tenant}.icims.com` portals are HTML-only; most redirect to this CXP platform.

<details><summary>Sample Outputs</summary>

GET https://careers.paychex.com/api/jobs?page=1 (jobs[] trimmed to 2)
---
```json
{
  "jobs": [
    {
      "data": {
        "slug": "44443",
        "language": "en-us",
        "languages": [
          "en-us"
        ],
        "client_code": "paychex",
        "req_id": "44443",
        "title": "Channel Sales Associate - Bank",
        "description": "<p style=\"margin: 0px;\">Paychex is reimagining how businesses manage their workforce by bringing payroll, HR, benefits, and advisory services together into a single connected HCM platform. As Paychex and Paycor come together, we're combining innovative technology, data-driven insights, AI, and human expertise to help organizations work smarter, support their people, and achieve better business outcomes. This is an exciting time to join our team as we continue to invest in innovation, simplify client experiences, and shape the future of work. At Paychex, you'll have the opportunity to grow your career, make a meaningful impact, and be part of something bigger as One Paychex.</p> <br><strong>Overview</strong><br><br><p style=\"margin: 0px;\"><strong><span style=\"font-size: 12pt;\">Lead with Relationships. Close with confidence.</span></strong></p><p style=\"margin: 0px;\"><br />Channel Sales is where growth happens fastest—for our clients and our people. You’ll work with businesses of 1–99 employees—the entrepreneurs powering local economies—helping them succeed with essential HR, payroll, and compliance solutions.</p><p style=\"margin: 0px;\"><br />Through trusted partners like Banks and client referrals, you won’t just sell—you’ll <strong>build relationships, unlock opportunities, and make a real impact</strong>. Every conversation matters, every referral opens a door, and every win accelerates your career.</p> <br><strong>Responsibilities</strong><br><br><p style=\"margin: 0px;\"><span style=\"font-size: 12pt;\"><strong>What You’ll Do</strong></span></p><ul><li>Generate new business through <strong>referral partners and outbound outreach</strong></li><li>Build relationships with <strong>bankers and existing clients</strong> to drive new logos </li><li>Execute <strong>high-volume daily activity</strong> (calls, follow-ups, and partner touchpoints) to build pipeline</li><li>Sell a full suite of solutions: <strong>payroll, HR, benefits, retirement, and compliance</strong></li><li>Manage a <strong>fast-paced sales cycle (1–2 weeks)</strong> with small business owners</li><li>Own the full process—from prospecting to closing deals</li></ul><p style=\"margin: 0px;\"><span style=\"font-size: 12pt;\"><strong>What It Takes to Win</strong></span></p><ul><li>Early-career sales professionals with 1-5 years of relevant experience.</li><li>Comfort with <strong>high activity, fast cycles, and transactional deal sizes</strong></li><li>Resilience, drive, and competitive motivation</li><li>Coachability and ability to quickly apply feedback</li><li>Experience working toward goals or quotas (sales or similar environments) and demonstrated leadership experience—whether through work, school, nonprofits, fraternities/sororities, clubs, or other organizations (for example, serving as a treasurer, team lead, or project lead)</li></ul><p style=\"margin: 0px;\"><span style=\"font-size: 12pt;\"><strong>Why This Role</strong></span></p><ul><li><strong>Uncapped earning potential</strong> + performance incentives and award trips</li><li><strong>Rapid skill development</strong> in a high-volume, high-growth sales environment</li><li>Strong access to opportunities through <strong>referral-based selling</strong></li><li>Real impact helping small businesses grow and stay compliant</li></ul><p style=\"margin: 0px;\"> </p><p style=\"margin: 0px;\">If you’re driven, coachable, and ready to build a high-growth sales career—this is your starting point.</p> <br><strong>Qualifications</strong><br><br><div><ul><li>H.S. Diploma - Required</li><li>Bachelor's Degree - Preferred</li><li>2 years of experience in Sales.</li><li>2 years of experience in B2B Sales.</li><li>Valid Driver's License - Required</li></ul></div> <br><strong>Compensation</strong><br><br>In the spirit of pay transparency, we are excited to share that the compensation range for this position is typically between $51,000 - $100,000. This range includes both base pay plus commissions/bonus but does not consider other components that make up the total rewards package for the position. If you are hired at Paychex, your overall compensation will be determined based on factors such as geographic location, skills, education, and/or experience which may result in total compensation outside of this range. <br><br><strong>Live the Paychex Values</strong><br><br><li>Act with uncompromising integrity.</li>\r\n<li>Provide outstanding service and build trusted relationships.</li>\r\n<li>Drive innovation in our products and services and continually improve our processes.</li>\r\n<li>Work in partnership and support each other.</li>\r\n<li>Be personally accountable and deliver on commitments.</li>\r\n<li>Treat each other with respect and dignity.</li> <br><strong>What's in it for you?</strong><br><br><ul><li>We value your well-being: We provide over 21 comprehensive rewards, including medical coverage, virtual wellness classes, tuition reimbursement, 401(k) + employer match, adoption assistance, financial assistance, and much more.</li>\r\n<li>We value your time: From paid time off to company holidays, culture days, and comprehensive work-life balance programs, we will ensure you have the flexibility you need to be your best.</li>\r\n<li>We value your development: Our award-winning training and development programs empower our employees with ongoing learning opportunities to give you the building blocks to grow your career.</li>\r\n<li>We value your perspective: Our company culture reflects the diversity of our employees. We want you to be you and your voice to be heard.</li>\r\n<li>We value our communities: We offer paid time off for volunteerism and promote many company-wide and local initiatives that benefit organizations you care about.</li>\r\n<br>Note: The benefits described apply to full-time employees. Benefits for part-time, contract, and intern roles may vary.</br> <br><strong>Not sure if you meet every requirement?</strong><br><br>At Paychex, we know that great talent comes in many forms. If you're passionate about the role but don't check every box, we still encourage you to apply. You might be the right fit - either for this position or another opportunity with us.<br></br>\r\nPaychex is an equal opportunity employer. We are committed to fostering a respectful and inclusive workplace where all individuals are treated fairly and evaluated based on their qualifications, experience, and merit. We comply with all applicable federal, state, and local laws prohibiting discrimination in employment.",
        "location_name": "Cleveland Territory",
        "city": "Cleveland",
        "state": "Ohio",
        "country": "United States",
        "country_code": "US",
        "postal_code": "44102",
        "location_type": "LAT_LNG",
        "latitude": 41.4735,
        "longitude": -81.7398,
        "categories": [
          {
            "name": "Sales"
          }
        ],
        "tags1": [
          "Paychex"
        ],
        "tags2": [
          "No"
        ],
        "tags3": [
          "Hybrid"
        ],
        "tags4": [
          "Full-Time"
        ],
        "department": "",
        "salary_value": 0,
        "salary_min_value": 0,
        "salary_max_value": 0,
        "employment_type": "FULL_TIME",
        "qualifications": "<div><ul><li>H.S. Diploma - Required</li><li>Bachelor's Degree - Preferred</li><li>2 years of experience in Sales.</li><li>2 years of experience in B2B Sales.</li><li>Valid Driver's License - Required</li></ul></div>",
        "hiring_organization": "Paychex",
        "responsibilities": "<p style=\"margin: 0px;\"><span style=\"font-size: 12pt;\"><strong>What You&rsquo;ll Do</strong></span></p><ul><li>Generate new business through <strong>referral partners and outbound outreach</strong></li><li>Build relationships with <strong>bankers and existing clients</strong> to drive new logos&nbsp;</li><li>Execute <strong>high-volume daily activity</strong> (calls, follow-ups, and partner touchpoints) to build pipeline</li><li>Sell a full suite of solutions: <strong>payroll, HR, benefits, retirement, and compliance</strong></li><li>Manage a <strong>fast-paced sales cycle (1&ndash;2 weeks)</strong> with small business owners</li><li>Own the full process&mdash;from prospecting to closing deals</li></ul><p style=\"margin: 0px;\"><span style=\"font-size: 12pt;\"><strong>What It Takes to Win</strong></span></p><ul><li>Early-career sales professionals with 1-5 years of relevant experience.</li><li>Comfort with <strong>high activity, fast cycles, and transactional deal sizes</strong></li><li>Resilience, drive, and competitive motivation</li><li>Coachability and ability to quickly apply feedback</li><li>Experience working toward goals or quotas (sales or similar environments) and demonstrated leadership experience&mdash;whether through work, school, nonprofits, fraternities/sororities, clubs, or other organizations (for example, serving as a treasurer, team lead, or project lead)</li></ul><p style=\"margin: 0px;\"><span style=\"font-size: 12pt;\"><strong>Why This Role</strong></span></p><ul><li><strong>Uncapped earning potential</strong> + performance incentives and award trips</li><li><strong>Rapid skill development</strong> in a high-volume, high-growth sales environment</li><li>Strong access to opportunities through <strong>referral-based selling</strong></li><li>Real impact helping small businesses grow and stay compliant</li></ul><p style=\"margin: 0px;\">&nbsp;</p><p style=\"margin: 0px;\">If you&rsquo;re driven, coachable, and ready to build a high-growth sales career&mdash;this is your starting point.</p>",
        "posted_date": "2026-07-30T20:09:00+0000",
        "apply_url": "https://careers-paychex.icims.com/jobs/44443/login",
        "internal": false,
        "external": false,
        "searchable": true,
        "applyable": true,
        "li_easy_applyable": true,
        "ats_code": "icims",
        "hiring_flow_name": "iCIMS ATS Hiring Flow",
        "isFrontLineAIJob": false,
        "meta_data": {
          "mongo": {
            "mongo_created": true
          },
          "openingjobs": {
            "openingJobId": "00009529f7b809123fb91286b2a5183c8eb8"
          },
          "icims": {
            "jps_is_public": true,
            "primary_posted_site_object": {
              "site": "careers-paychex",
              "tenantId": "12446",
              "siteId": "ffc030a9-1c83-45b2-bab4-b26bad300d6d",
              "datePosted": "2026-07-30T20:09:00+0000",
              "siteType": "ATTRACT"
            },
            "date_updated": "2026-07-30T20:09:55Z",
            "config_keys": {
              "jobposting.external.company.name": "Paychex",
              "jobposting.external.indeed.screenerquestions": "0",
              "jobposting.external.company.url": "https://www.paychex.com/",
              "icims.external.xml.feeds": "icims.adzuna.xml.enabled,icims.aeroemploiformation.xml.enabled,icims.apec.xml.enabled,icims.appcast.xml.enabled,icims.bayard.xml.enabled,icims.careerbuilder.xml.enabled,icims.careerarc.xml.enabled,icims.clearbold.xml.enabled,icims.constructionjobs.xml.enabled,icims.craigslist.xml.enabled,icims.cv-library.xml.enabled,icims.cvaden.xml.enabled,icims.direct-employers.xml.enabled,icims.hireheroesusa.xml.enabled,icims.hirepurpose.xml.enabled,icims.indeed.xml.enabled,icims.inhersight.xml.enabled,icims.itjobscafe.xml.enabled,icims.jobbio.xml.enabled,icims.jobboardio.xml.enabled,icims.linkedin.xml.enabled,icims.mogul.xml.enabled,icims.monster.xml.enabled,icims.msep.xml.enabled,icims.oneten.xml.enabled,icims.pallet-labs.xml.enabled,icims.paradox.xml.enabled,icims.powertofly.xml.enabled,icims.prideatwork.xml.enabled,icims.professionaldiversitynetwork.xml.enabled,icims.propellum.xml.enabled,icims.qkly.xml.enabled,icims.radancy.xml.enabled,icims.recruitnet.xml.enabled,icims.recruitics.xml.enabled,icims.recruitmilitary.xml.enabled,icims.regionsjob.xml.enabled,icims.resume-library.xml.enabled,icims.snagajob.xml.enabled,icims.upward.xml.enabled,icims.webengineering.xml.enabled,icims.ziprecruiter.xml.enabled,icims.nexxt.xml.enabled",
              "portal.id": "17",
              "ccc.attract.portal.url": "https://careers.paychex.com/careers",
              "icims.config.web.indeed.easy.apply": "0"
            },
            "revision_int": 1,
            "uuid": "41db11b3-ae93-4172-ae79-6945d21fc883"
          },
          "ats_job_hash": "2c744799d737bd3ecdd103981fac5be7",
          "googlejobs": {
            "jobName": "projects/helpful-passage-853/tenants/cb22eb5b-7e00-0000-0000-007edad744d3/jobs/85473035036828358"
          },
          "import_id": "7f24399b-33e2-460f-80cd-61a0c24ac58b",
          "redirectOnApply": true,
          "questionservice": {
            "id": "45757096"
          },
          "canonical_url": "https://careers.paychex.com/jobs/44443?lang=en-us",
          "gdpr": false,
          "last_mod": "2026-07-30T20:23:11+0000",
          "elasticsearch": {
            "es_created": false
          },
          "opensearch": {
            "hit_score": "NaN",
            "hit_explanation": null,
            "hit_matched_queries": [
              "internal",
              "applyable"
            ],
            "hit_index": "jobs",
            "hit_shard": null
          },
          "import_source": "ImporterService",
          "client_code": "paychex"
        },
        "update_date": "2026-07-30T20:23:11+0000",
        "create_date": "2026-07-30T20:10:21+0000",
        "category": [
          " Sales"
        ],
        "full_location": "Cleveland, Ohio",
        "short_location": "Cleveland, Ohio",
        "multipleLocations": false
      }
    },
    {
      "data": {
        "slug": "44440",
        "language": "en-us",
        "languages": [
          "en-us"
        ],
        "client_code": "paychex",
        "req_id": "44440",
        "title": "Service Representative - MSP",
        "description": "Paychex is reimagining how businesses manage their workforce by bringing payroll, HR, benefits, and advisory services together into a single connected HCM platform. As Paychex and Paycor come together, we're combining innovative technology, data-driven insights, AI, and human expertise to help organizations work smarter, support their people, and achieve better business outcomes. This is an exciting time to join our team as we continue to invest in innovation, simplify client experiences, and shape the future of work. At Paychex, you'll have the opportunity to grow your career, make a meaningful impact, and be part of something bigger as One Paychex. <br><strong>Overview</strong><br><br><p style=\"margin: 0px;\">Under a significant level of guidance and direction, provides customer service for one or more Paychex products. Uses prescribed guidelines to ensure good customer relations are maintained and customer claims and complaints are resolved fairly, effectively, and in accordance with Paychex policies and procedures.</p> <br><strong>Responsibilities</strong><br><br><ul><li>Delivers quality service to Paychex clients via phone, email, chat, and other avenues</li><li>Follows up on outstanding items to issue completion in compliance with established Service Level Agreements</li><li>Uses systems and software packages, which may include HRIS, Salesforce, Flex, Core Advanced, and ORS</li><li>Develops and maintains a basic working knowledge of one or more Paychex products</li><li>Performs data input, maintaining strong prioritization and organization</li><li>Documents all client interactions</li></ul> <br><strong>Qualifications</strong><br><br><div><ul><li>Bachelor's Degree - Preferred</li></ul></div> <br><br><strong>Live the Paychex Values</strong><br><br><li>Act with uncompromising integrity.</li>\r\n<li>Provide outstanding service and build trusted relationships.</li>\r\n<li>Drive innovation in our products and services and continually improve our processes.</li>\r\n<li>Work in partnership and support each other.</li>\r\n<li>Be personally accountable and deliver on commitments.</li>\r\n<li>Treat each other with respect and dignity.</li> <br><strong>What's in it for you?</strong><br><br><ul><li>We value your well-being: We provide over 21 comprehensive rewards, including medical coverage, virtual wellness classes, tuition reimbursement, 401(k) + employer match, adoption assistance, financial assistance, and much more.</li>\r\n<li>We value your time: From paid time off to company holidays, culture days, and comprehensive work-life balance programs, we will ensure you have the flexibility you need to be your best.</li>\r\n<li>We value your development: Our award-winning training and development programs empower our employees with ongoing learning opportunities to give you the building blocks to grow your career.</li>\r\n<li>We value your perspective: Our company culture reflects the diversity of our employees. We want you to be you and your voice to be heard.</li>\r\n<li>We value our communities: We offer paid time off for volunteerism and promote many company-wide and local initiatives that benefit organizations you care about.</li>\r\n<br>Note: The benefits described apply to full-time employees. Benefits for part-time, contract, and intern roles may vary.</br> <br><strong>Not sure if you meet every requirement?</strong><br><br>At Paychex, we know that great talent comes in many forms. If you're passionate about the role but don't check every box, we still encourage you to apply. You might be the right fit - either for this position or another opportunity with us.<br></br>\r\nPaychex is an equal opportunity employer. We are committed to fostering a respectful and inclusive workplace where all individuals are treated fairly and evaluated based on their qualifications, experience, and merit. We comply with all applicable federal, state, and local laws prohibiting discrimination in employment.",
        "location_name": "Rochester University Park Office",
        "street_address": "1175 John Street",
        "city": "West Henrietta",
        "state": "New York",
        "country": "United States",
        "country_code": "US",
        "postal_code": "14586",
        "location_type": "LAT_LNG",
        "latitude": 43.0397,
        "longitude": -77.6871,
        "additional_locations": [
          {
            "location_name": "Phoenix Office",
            "street_address": "16404 N Black Canyon Hwy\n250",
            "city": "Phoenix",
            "state": "Arizona",
            "country": "United States",
            "country_code": "US",
            "postal_code": "85053",
            "latitude": 33.6299,
            "longitude": -112.1316
          }
        ],
        "categories": [
          {
            "name": "Customer Service and Support"
          }
        ],
        "tags1": [
          "Paychex"
        ],
        "tags2": [
          "No"
        ],
        "tags3": [
          "Onsite"
        ],
        "tags4": [
          "Full-Time"
        ],
        "department": "",
        "salary_value": 0,
        "salary_min_value": 0,
        "salary_max_value": 0,
        "employment_type": "FULL_TIME",
        "qualifications": "<div><ul><li>Bachelor's Degree - Preferred</li></ul></div>",
        "hiring_organization": "Paychex",
        "responsibilities": "<ul><li>Delivers quality service to Paychex clients via phone, email, chat, and other avenues</li><li>Follows up on outstanding items to issue completion in compliance with established Service Level Agreements</li><li>Uses systems and software packages, which may include HRIS, Salesforce, Flex, Core Advanced, and ORS</li><li>Develops and maintains a basic working knowledge of one or more Paychex products</li><li>Performs data input, maintaining strong prioritization and organization</li><li>Documents all client interactions</li></ul>",
        "posted_date": "2026-07-30T20:02:00+0000",
        "apply_url": "https://careers-paychex.icims.com/jobs/44440/login",
        "internal": false,
        "external": false,
        "searchable": true,
        "applyable": true,
        "li_easy_applyable": true,
        "ats_code": "icims",
        "hiring_flow_name": "iCIMS ATS Hiring Flow",
        "isFrontLineAIJob": false,
        "meta_data": {
          "mongo": {
            "mongo_created": true
          },
          "openingjobs": {
            "openingJobId": "0000e2d63dc17f3d4fd183a5d00ef5e88754"
          },
          "icims": {
            "jps_is_public": true,
            "primary_posted_site_object": {
              "site": "careers-paychex",
              "tenantId": "12446",
              "siteId": "ffc030a9-1c83-45b2-bab4-b26bad300d6d",
              "datePosted": "2026-07-30T20:02:00+0000",
              "siteType": "ATTRACT"
            },
            "date_updated": "2026-07-30T20:02:47Z",
            "config_keys": {
              "jobposting.external.company.name": "Paychex",
              "jobposting.external.indeed.screenerquestions": "0",
              "jobposting.external.company.url": "https://www.paychex.com/",
              "icims.external.xml.feeds": "icims.adzuna.xml.enabled,icims.aeroemploiformation.xml.enabled,icims.apec.xml.enabled,icims.appcast.xml.enabled,icims.bayard.xml.enabled,icims.careerbuilder.xml.enabled,icims.careerarc.xml.enabled,icims.clearbold.xml.enabled,icims.constructionjobs.xml.enabled,icims.craigslist.xml.enabled,icims.cv-library.xml.enabled,icims.cvaden.xml.enabled,icims.direct-employers.xml.enabled,icims.hireheroesusa.xml.enabled,icims.hirepurpose.xml.enabled,icims.indeed.xml.enabled,icims.inhersight.xml.enabled,icims.itjobscafe.xml.enabled,icims.jobbio.xml.enabled,icims.jobboardio.xml.enabled,icims.linkedin.xml.enabled,icims.mogul.xml.enabled,icims.monster.xml.enabled,icims.msep.xml.enabled,icims.oneten.xml.enabled,icims.pallet-labs.xml.enabled,icims.paradox.xml.enabled,icims.powertofly.xml.enabled,icims.prideatwork.xml.enabled,icims.professionaldiversitynetwork.xml.enabled,icims.propellum.xml.enabled,icims.qkly.xml.enabled,icims.radancy.xml.enabled,icims.recruitnet.xml.enabled,icims.recruitics.xml.enabled,icims.recruitmilitary.xml.enabled,icims.regionsjob.xml.enabled,icims.resume-library.xml.enabled,icims.snagajob.xml.enabled,icims.upward.xml.enabled,icims.webengineering.xml.enabled,icims.ziprecruiter.xml.enabled,icims.nexxt.xml.enabled",
              "portal.id": "17",
              "ccc.attract.portal.url": "https://careers.paychex.com/careers",
              "icims.config.web.indeed.easy.apply": "0"
            },
            "revision_int": 1,
            "uuid": "05f9f4c5-9419-4732-850d-23d4280c3e11"
          },
          "ats_job_hash": "601a00a6b528fc97a524528093a31fdb",
          "googlejobs": {
            "jobName": "projects/helpful-passage-853/tenants/cb22eb5b-7e00-0000-0000-007edad744d3/jobs/88349357455090374"
          },
          "import_id": "7f24399b-33e2-460f-80cd-61a0c24ac58b",
          "redirectOnApply": true,
          "questionservice": {
            "id": "45756941"
          },
          "canonical_url": "https://careers.paychex.com/jobs/44440?lang=en-us",
          "gdpr": false,
          "last_mod": "2026-07-30T20:23:11+0000",
          "elasticsearch": {
            "es_created": false
          },
          "opensearch": {
            "hit_score": "NaN",
            "hit_explanation": null,
            "hit_matched_queries": [
              "internal",
              "applyable"
            ],
            "hit_index": "jobs",
            "hit_shard": null
          },
          "import_source": "ImporterService",
          "client_code": "paychex"
        },
        "update_date": "2026-07-30T20:23:11+0000",
        "create_date": "2026-07-30T20:03:22+0000",
        "category": [
          " Customer Service and Support"
        ],
        "full_location": "West Henrietta, New York; Phoenix, Arizona",
        "short_location": "West Henrietta, New York",
        "multipleLocations": true
      }
    }
  ],
  "locations": [],
  "totalCount": 578,
  "request_id": false,
  "meta_data": false,
  "count": 578,
  "languageCounts": {
    "en-us": {
      "displayName": "English",
      "count": 578
    }
  },
  "filter": {
    "facetList": {
      "employment_type": [
        {
          "term": "FULL_TIME",
          "count": 577
        },
        {
          "term": "PART_TIME",
          "count": 1
        }
      ],
      "tags1": [
        {
          "term": "Paychex",
          "count": 478
        },
        {
          "term": "Paycor",
          "count": 93
        }
      ],
      "tags3": [
        {
          "term": "Hybrid",
          "count": 481
        },
        {
          "term": "Remote",
          "count": 49
        }
      ]
    },
    "displayLimit": 10,
    "brands": {
      "all": [],
      "shortlist": []
    },
    "categories": {
      "all": [
        {
          "category": "Administrative",
          "numJobs": 4
        },
        {
          "category": "Corporate and Product Strategy",
          "numJobs": 1
        }
      ],
      "shortlist": [
        {
          "category": "Administrative",
          "numJobs": 4
        },
        {
          "category": "Corporate and Product Strategy",
          "numJobs": 1
        }
      ]
    },
    "locations": {
      "all": [
        {
          "city": "Akron",
          "state": "Ohio",
          "country": "United States",
          "count": 1
        },
        {
          "city": "Albany",
          "state": "New York",
          "country": "United States",
          "count": 4
        }
      ],
      "shortlist": [
        {
          "city": "Akron",
          "state": "Ohio",
          "country": "United States",
          "count": 1
        },
        {
          "city": "Albany",
          "state": "New York",
          "country": "United States",
          "count": 4
        }
      ]
    },
    "experienceLevels": {
      "all": [],
      "shortlist": []
    }
  }
}
```
</details>





# Oracle/Taleo
**Pattern**: `https://{host}.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true&finder=findReqs;siteNumber={site},facetsList=LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS,limit=25,sortBy=POSTING_DATES_DESC`
* This is Oracle Recruiting Cloud (ORC). Host + site number are in the careers URL: `{host}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/{site}` (e.g. careers.oracle.com → eeho.fa.us2.oraclecloud.com, CX_45001).
* The `facetsList` param is required — without it `requisitionList` comes back empty.
* List items are summaries; jobs live in `items[0].requisitionList[]`, total in `items[0].TotalJobsCount`.
* Detail: `.../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;siteNumber={site},Id="{Id}"` — adds `ExternalDescriptionStr`, `ExternalQualificationsStr`, `ExternalResponsibilitiesStr`, locations, flex fields.
* Classic Taleo (`{company}.taleo.net/careersection/...`) appears fully retired — every tenant probed (aa, emerson, capgemini, tjx, dtcc, bnsf) no longer resolves. Treat Oracle = ORC.

<details><summary>Sample Outputs</summary>

GET .../recruitingCEJobRequisitions?...siteNumber=CX_45001,...,limit=1,sortBy=POSTING_DATES_DESC (facet values trimmed to 2)
---
```json
{
  "items": [
    {
      "SearchId": 1,
      "Keyword": null,
      "CorrectedKeyword": null,
      "UseExactKeywordFlag": false,
      "SuggestedKeyword": null,
      "ExecuteSpellCheckFlag": true,
      "Location": null,
      "LocationId": null,
      "Radius": 0,
      "RadiusUnit": "MI",
      "SelectedTitlesFacet": null,
      "SelectedCategoriesFacet": null,
      "SelectedPostingDatesFacet": null,
      "SelectedLocationsFacet": null,
      "LastSelectedFacet": null,
      "Facets": "LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS",
      "Offset": 0,
      "Limit": 1,
      "SortBy": "POSTING_DATES_DESC",
      "TotalJobsCount": 2302,
      "Latitude": null,
      "Longitude": null,
      "SiteNumber": "CX_45001",
      "JobFamilyId": null,
      "PostingStartDate": null,
      "PostingEndDate": null,
      "SelectedWorkLocationsFacet": null,
      "RequisitionId": null,
      "CandidateNumber": null,
      "WorkLocationZipCode": null,
      "WorkLocationCountryCode": null,
      "SelectedFlexFieldsFacets": null,
      "OrganizationId": null,
      "SelectedOrganizationsFacet": null,
      "UserTargetFacetName": null,
      "UserTargetFacetInputTerm": null,
      "HotJobFlag": null,
      "WorkplaceType": null,
      "SelectedWorkplaceTypesFacet": null,
      "BotQRShortCode": null,
      "requisitionList": [
        {
          "Id": "330043",
          "Title": "Senior Manager, Data Center Operations — Critical Environments (Multi-Site)",
          "PostedDate": "2026-07-30",
          "PostingEndDate": null,
          "Language": "US",
          "PrimaryLocationCountry": "US",
          "GeographyId": 100000000729409,
          "HotJobFlag": true,
          "WorkplaceTypeCode": null,
          "JobFamily": null,
          "JobFunction": null,
          "WorkerType": null,
          "ContractType": null,
          "ManagerLevel": null,
          "JobSchedule": null,
          "JobShift": null,
          "JobType": null,
          "StudyLevel": null,
          "DomesticTravelRequired": null,
          "InternationalTravelRequired": null,
          "WorkDurationYears": null,
          "WorkDurationMonths": null,
          "WorkHours": null,
          "WorkDays": null,
          "LegalEmployer": null,
          "BusinessUnit": null,
          "Department": null,
          "Organization": null,
          "BusinessUnitId": 300001096431127,
          "LegalEmployerId": 300000000482027,
          "OrganizationId": 300001902541887,
          "MediaThumbURL": "https://img.youtube.com/vi/BV0GMCITXg4/hqdefault.jpg",
          "ShortDescriptionStr": "Oversees Data Center Operations across multiple data centers, managing and integrating team performance, operations metrics, and capacity analysis into the broader business line strategy.",
          "PrimaryLocation": "Abilene, TX, United States",
          "Distance": 1785369600000.0,
          "TrendingFlag": false,
          "BeFirstToApplyFlag": true,
          "Relevancy": 15,
          "WorkplaceType": "",
          "ExternalQualificationsStr": null,
          "ExternalResponsibilitiesStr": null,
          "secondaryLocations": []
        }
      ],
      "categoriesFacet": [
        {
          "Id": 302958497961752,
          "Name": "Product and Research",
          "TotalCount": 538
        },
        {
          "Id": 300000001917356,
          "Name": "Product Development",
          "TotalCount": 368
        }
      ],
      "locationsFacet": [
        {
          "Id": 300000000149325,
          "Name": "United States",
          "TotalCount": 1515
        },
        {
          "Id": 100000000686062,
          "Name": "TN, United States",
          "TotalCount": 338
        }
      ],
      "postingDatesFacet": [
        {
          "Id": 7,
          "Name": "Less than 7 days",
          "TotalCount": 339
        },
        {
          "Id": 30,
          "Name": "Less than 30 days",
          "TotalCount": 1268
        },
        {
          "Id": 31,
          "Name": "Greater than 30 days",
          "TotalCount": 1034
        }
      ],
      "titlesFacet": [
        {
          "Id": "SWE",
          "Name": "Software Engineering",
          "TotalCount": 301
        },
        {
          "Id": "PRODEV-SWENG",
          "Name": "PRODEV-SWENG",
          "TotalCount": 264
        }
      ],
      "workLocationsFacet": [],
      "flexFieldsFacet": [
        {
          "Name": "AttributeChar29",
          "Label": "Role"
        },
        {
          "Name": "AttributeChar4",
          "Label": "Job Type"
        }
      ],
      "organizationsFacet": [
        {
          "Id": 300001902541887,
          "Name": "CLBPTS - All Lines of Business",
          "TotalCount": 2302
        }
      ],
      "workplaceTypesFacet": [
        {
          "Id": "ORA_ON_SITE",
          "Name": "On-site",
          "TotalCount": 2302
        }
      ]
    }
  ],
  "count": 1,
  "hasMore": false,
  "limit": 200,
  "offset": 0,
  "links": [
    {
      "rel": "self",
      "href": "https://eeho.fa.us2.oraclecloud.com:443/hcmRestApi/resources/11.13.18.05/recruitingCEJobRequisitions",
      "name": "recruitingCEJobRequisitions",
      "kind": "collection"
    },
    {
      "rel": "action",
      "href": "https://eeho.fa.us2.oraclecloud.com:443/hcmRestApi/resources/11.13.18.05/recruitingCEJobRequisitions/action/getRequisitionsLocationCoordinates",
      "name": "getRequisitionsLocationCoordinates",
      "kind": "other"
    },
    {
      "rel": "action",
      "href": "https://eeho.fa.us2.oraclecloud.com:443/hcmRestApi/resources/11.13.18.05/recruitingCEJobRequisitions/action/getRequisitionDetailsForMap",
      "name": "getRequisitionDetailsForMap",
      "kind": "other"
    }
  ]
}
```
GET .../recruitingCEJobRequisitionDetails?expand=all&onlyData=true&finder=ById;siteNumber=CX_45001,Id="330043"
---
```json
{
  "items": [
    {
      "Id": "330043",
      "Title": "Senior Manager, Data Center Operations — Critical Environments (Multi-Site)",
      "Category": "Technology Operations",
      "RequisitionType": "Professional",
      "JobGrade": null,
      "RequisitionId": 302963961859554,
      "ExternalPostedStartDate": "2026-07-30T14:48:09+00:00",
      "JobLevel": null,
      "JobSchedule": null,
      "JobShift": null,
      "StudyLevel": null,
      "InternationalTravelRequired": null,
      "ExternalContactName": null,
      "ExternalContactEmail": null,
      "ContractType": null,
      "ExternalPostedEndDate": null,
      "JobFamilyId": 302958497961746,
      "GeographyId": 100000000729409,
      "GeographyNodeId": 100257187669506,
      "ExternalDescriptionStr": "<ul style=\" border-color: var(--color-gray-200,currentColor);  border-image: none;  border-style: solid;  border-width: 0px;  box-sizing: border-box;  font-style: normal;  font-weight: 400;  margin-block: calc(var(--spacing)*0);  margin-bottom: 1.25em;  margin-right: 0px;  margin-top: 1.25em;  padding-inline-start: 1.625em;  padding: 0px;  text-decoration-line: none;  text-decoration-style: solid;  text-decoration-thickness: auto\">\n <li style=\"border-color: var(--color-gray-200,currentColor);  border-image: none;  border-style: solid;  border-width: 0px;  box-sizing: border-box;  margin-block: calc(var(--spacing)*0);  margin-bottom: 0.5em;  margin-right: 0px;  margin-top: 0.5em;  padding-inline-start: 0.375em;  padding: 0px\">\n  <p style=\"margin-left: 0px\">Lead multi-site Critical Environments operations, ensuring safe, consistent execution across electrical and mechanical infrastructure that powers and cools Oracle’s cloud (power distribution, UPS, generators, switchgear, cooling plants/CRAHs, controls/BMS, and airflow/containment) across multiple data center locations.</p></li>\n</ul>",
      "CorporateDescriptionStr": "<p style=\"margin-bottom:12pt\">Only Oracle brings together the data, infrastructure, applications, and expertise to power everything from industry innovations to life-saving care. And with AI embedded across our products and services, we help customers turn that promise into a better future for all. Discover your potential at a company leading the way in AI and cloud solutions that impact billions of lives.</p>\n<p style=\"margin-bottom:12pt\">True innovation starts when everyone is empowered to contribute. That’s why we’re committed to growing a workforce that promotes opportunities for all with competitive benefits that support our people with flexible medical, life insurance, and retirement options. We also encourage employees to give back to their communities through our volunteer programs.</p>\n<p style=\"margin-bottom:12pt\">We’re committed to including people with disabilities at all stages of the employment process. If you require accessibility assistance or accommodation for a disability at any point, let us know by emailing <u><a href=\"mailto:accommodation-request_mb@oracle.com\" target=\"_blank\" rel=\"nofollow\">accommodation-request_mb@oracle.com</a></u> or by calling 1-888-404-2494 in the United States.</p>\n<p style=\"margin-bottom:12pt\">Oracle is an Equal Employment Opportunity Employer. All qualified applicants will receive consideration for employment without regard to race, color, religion, sex, national origin, sexual orientation, gender identity, disability and protected veterans’ status, or any other characteristic protected by law. Oracle will consider for employment qualified applicants with arrest and conviction records pursuant to applicable law.</p>",
      "OrganizationDescriptionStr": "",
      "ShortDescriptionStr": "Oversees Data Center Operations across multiple data centers, managing and integrating team performance, operations metrics, and capacity analysis into the broader business line strategy.",
      "ContentLocale": "en",
      "PrimaryLocation": "Abilene, TX, United States",
      "PrimaryLocationCountry": "US",
      "ObjectVerNumberProfile": "1",
      "ApplyWhenNotPostedFlag": true,
      "ExternalQualificationsStr": "Disclaimer:<br><br><b>Certain U.S. based or U.S. customer or client-facing roles may be required to comply with applicable requirements, such as immunization/occupational health mandates, and/or drug testing requirements.</b><br><br><b>Range and benefit information provided in this posting are specific to the stated locations only</b><br><br>US: Hiring Range in USD from: $126,200 to $264,100 per annum. May be eligible for bonus, equity, and compensation deferral.<br><br><br>Oracle maintains broad salary ranges for its roles in order to account for variations in knowledge, skills, experience, market conditions and locations, as well as reflect Oracle's differing products, industries and lines of business.<br>Candidates are typically placed into the range based on the preceding factors as well as internal peer equity.<br><br>Oracle US offers a comprehensive benefits package which includes the following:<br>1.   Medical, dental, and vision insurance, including expert medical opinion<br>2.   Short term disability and long term disability<br>3.   Life insurance and AD&D<br>4.   Supplemental life insurance (Employee/Spouse/Child)<br>5.   Health care and dependent care Flexible Spending Accounts<br>6.   Pre-tax commuter and parking benefits<br>7.   401(k) Savings and Investment Plan with company match<br>8.   Paid time off: Flexible Vacation is provided to all eligible employees assigned to a salaried (non-overtime eligible) position. Accrued Vacation is provided to all other employees eligible for vacation benefits. For employees working at least 35 hours per week, the vacation accrual rate is 13 days annually for the first three years of employment and 18 days annually for subsequent years of employment. Vacation accrual is prorated for employees working between 20 and 34 hours per week. Employees working fewer than 20 hours per week are not eligible for vacation.<br>9.   11 paid holidays<br>10.  Paid sick leave: 72 hours of paid sick leave upon date of hire. Refreshes each calendar year. Unused balance will carry over each year up to a maximum cap of 112 hours.<br>11.  Paid parental leave<br>12.  Adoption assistance<br>13.  Employee Stock Purchase Plan<br>14.  Financial planning and group legal<br>15.  Voluntary benefits including auto, homeowner and pet insurance<br><br>The role will generally accept applications for at least three calendar days from the posting date or as long as the job remains posted.<p>Career Level - M3</p><br>",
      "InternalQualificationsStr": "Disclaimer:<br><br><b>Certain U.S. based or U.S. customer or client-facing roles may be required to comply with applicable requirements, such as immunization/occupational health mandates, and/or drug testing requirements.</b><br><br><b>Range and benefit information provided in this posting are specific to the stated locations only</b><br><br>US: Hiring Range in USD from: $126,200 to $264,100 per annum. May be eligible for bonus, equity, and compensation deferral.<br><br><br>Oracle maintains broad salary ranges for its roles in order to account for variations in knowledge, skills, experience, market conditions and locations, as well as reflect Oracle's differing products, industries and lines of business.<br>Candidates are typically placed into the range based on the preceding factors as well as internal peer equity.<br><br>Oracle US offers a comprehensive benefits package which includes the following:<br>1.   Medical, dental, and vision insurance, including expert medical opinion<br>2.   Short term disability and long term disability<br>3.   Life insurance and AD&D<br>4.   Supplemental life insurance (Employee/Spouse/Child)<br>5.   Health care and dependent care Flexible Spending Accounts<br>6.   Pre-tax commuter and parking benefits<br>7.   401(k) Savings and Investment Plan with company match<br>8.   Paid time off: Flexible Vacation is provided to all eligible employees assigned to a salaried (non-overtime eligible) position. Accrued Vacation is provided to all other employees eligible for vacation benefits. For employees working at least 35 hours per week, the vacation accrual rate is 13 days annually for the first three years of employment and 18 days annually for subsequent years of employment. Vacation accrual is prorated for employees working between 20 and 34 hours per week. Employees working fewer than 20 hours per week are not eligible for vacation.<br>9.   11 paid holidays<br>10.  Paid sick leave: 72 hours of paid sick leave upon date of hire. Refreshes each calendar year. Unused balance will carry over each year up to a maximum cap of 112 hours.<br>11.  Paid parental leave<br>12.  Adoption assistance<br>13.  Employee Stock Purchase Plan<br>14.  Financial planning and group legal<br>15.  Voluntary benefits including auto, homeowner and pet insurance<br><br>The role will generally accept applications for at least three calendar days from the posting date or as long as the job remains posted.<p>Career Level - M3</p><br>",
      "InternalResponsibilitiesStr": "<h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">What you’ll do</h2><ul class=\"\" dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; list-style: outside; margin-block: calc(var(--spacing)*0); margin-bottom: 1.25em; margin-right: 0px; margin-top: 0px; orphans: 2; padding-inline-start: 1.625em; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\"><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Lead multi-site <strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Critical Environments</strong> operations, ensuring rigorous execution across electrical and mechanical infrastructure (power distribution, UPS, generators, switchgear, cooling plants, pumps/CRAHs/CRACs, controls/BMS, containment/airflow).</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Set the operational cadence (site health reviews, PM/CM execution, MOP/SOP/EOP readiness, risk registers, KPI reviews) to prevent downtime and reduce operational risk.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Direct response to CE events and escalations—coordinating rapid triage, clear communications, safe restoration, and post-incident follow-through.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Drive disciplined <strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">RCA/CAPA</strong>, ensuring corrective actions are prioritized, tracked, verified, and embedded into standard work.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Standardize processes, documentation, and knowledge management so shifts and sites operate consistently to world-class CE standards.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Partner with Engineering and Projects to support <strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">new builds and expansions</strong>, ensuring CE design intent is operationalized (commissioning readiness, handoff criteria, spares strategy, training, and runbook completeness).</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Identify opportunities for automation and monitoring improvements (alarms, trending, predictive maintenance signals) to improve time-to-detect and prevent repeat events.</p></li></ul><div class=\"my-2\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*2); margin: 0px; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">&nbsp;</div><hr><h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">What you’ll own</h2><ul class=\"\" dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; list-style: outside; margin-block: calc(var(--spacing)*0); margin-bottom: 1.25em; margin-right: 0px; margin-top: 0px; orphans: 2; padding-inline-start: 1.625em; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\"><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Availability and facilities risk posture</strong> across a portfolio of data centers—owning execution that protects uptime and life-safety.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Capacity visibility</strong> for power and cooling (and associated constraints), providing actionable inputs to portfolio planning and expansion readiness.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Maintenance excellence:</strong> PM quality, repair execution, vendor coordination, and lifecycle planning that improves reliability and reduces avoidable events.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Operational readiness artifacts:</strong> MOP/SOP/EOP quality, change control rigor, lockout/tagout alignment, and a high-integrity CE knowledge base.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Efficiency and stability improvements</strong> (airflow/containment, pressure management, power-train resilience, control system tuning) that strengthen long-term performance.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">People leadership:</strong> coaching CE managers/leads and technicians; performance management; hiring and development of a high-performing, safety-first CE organization.</p></li></ul><div class=\"my-2\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*2); margin: 0px; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">&nbsp;</div><hr><h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Why it matters</h2><p dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*0); margin: 0px 0px 1.25em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Oracle Cloud Infrastructure supports always-on services where reliability is non-negotiable. Critical Environments is the foundation—when power or cooling is unstable, everything is at risk. This role is a hands-on operational leadership position with direct impact on uptime, safety, and the ability to scale. You’ll turn facility signals into preventative action, lead through high-stakes events, and raise operational maturity across multiple sites.</p><div class=\"my-2\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*2); margin: 0px; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">&nbsp;</div><hr><h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Why Abilene</h2><p dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*0); margin: 0px 0px 1.25em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Abilene is a key, growing location in Oracle’s data center footprint—offering meaningful ownership and visibility. You’ll lead critical infrastructure operations today while helping bring additional capacity online with the operational rigor required for world-class reliability. For CE leaders who want to build, improve, and operate at scale, Abilene offers an opportunity to make a measurable impact.</p>",
      "ExternalResponsibilitiesStr": "<h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">What you’ll do</h2><ul class=\"\" dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; list-style: outside; margin-block: calc(var(--spacing)*0); margin-bottom: 1.25em; margin-right: 0px; margin-top: 0px; orphans: 2; padding-inline-start: 1.625em; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\"><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Lead multi-site <strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Critical Environments</strong> operations, ensuring rigorous execution across electrical and mechanical infrastructure (power distribution, UPS, generators, switchgear, cooling plants, pumps/CRAHs/CRACs, controls/BMS, containment/airflow).</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Set the operational cadence (site health reviews, PM/CM execution, MOP/SOP/EOP readiness, risk registers, KPI reviews) to prevent downtime and reduce operational risk.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Direct response to CE events and escalations—coordinating rapid triage, clear communications, safe restoration, and post-incident follow-through.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Drive disciplined <strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">RCA/CAPA</strong>, ensuring corrective actions are prioritized, tracked, verified, and embedded into standard work.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Standardize processes, documentation, and knowledge management so shifts and sites operate consistently to world-class CE standards.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Partner with Engineering and Projects to support <strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">new builds and expansions</strong>, ensuring CE design intent is operationalized (commissioning readiness, handoff criteria, spares strategy, training, and runbook completeness).</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\">Identify opportunities for automation and monitoring improvements (alarms, trending, predictive maintenance signals) to improve time-to-detect and prevent repeat events.</p></li></ul><div class=\"my-2\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*2); margin: 0px; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">&nbsp;</div><hr><h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">What you’ll own</h2><ul class=\"\" dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; list-style: outside; margin-block: calc(var(--spacing)*0); margin-bottom: 1.25em; margin-right: 0px; margin-top: 0px; orphans: 2; padding-inline-start: 1.625em; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\"><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Availability and facilities risk posture</strong> across a portfolio of data centers—owning execution that protects uptime and life-safety.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Capacity visibility</strong> for power and cooling (and associated constraints), providing actionable inputs to portfolio planning and expansion readiness.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Maintenance excellence:</strong> PM quality, repair execution, vendor coordination, and lifecycle planning that improves reliability and reduces avoidable events.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Operational readiness artifacts:</strong> MOP/SOP/EOP quality, change control rigor, lockout/tagout alignment, and a high-integrity CE knowledge base.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">Efficiency and stability improvements</strong> (airflow/containment, pressure management, power-train resilience, control system tuning) that strengthen long-term performance.</p></li><li class=\"text-start \" style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; margin-block: calc(var(--spacing)*0); margin-bottom: 0.5em; margin-right: 0px; margin-top: 0.5em; padding-inline-start: 0.375em; padding: 0px; text-align: start;\"><p style=\"margin-left: 0px;\"><strong style=\"border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; color: var(--tw-prose-bold); margin: 0px; padding: 0px;\">People leadership:</strong> coaching CE managers/leads and technicians; performance management; hiring and development of a high-performing, safety-first CE organization.</p></li></ul><div class=\"my-2\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*2); margin: 0px; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">&nbsp;</div><hr><h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Why it matters</h2><p dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*0); margin: 0px 0px 1.25em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Oracle Cloud Infrastructure supports always-on services where reliability is non-negotiable. Critical Environments is the foundation—when power or cooling is unstable, everything is at risk. This role is a hands-on operational leadership position with direct impact on uptime, safety, and the ability to scale. You’ll turn facility signals into preventative action, lead through high-stakes events, and raise operational maturity across multiple sites.</p><div class=\"my-2\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*2); margin: 0px; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">&nbsp;</div><hr><h2 dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(0, 0, 0); color: var(--tw-prose-headings); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 1.5em; font-style: normal; font-variant-caps: normal; font-weight: var(--font-weight-semibold); letter-spacing: normal; line-height: 1.33333; margin-block: calc(var(--spacing)*1); margin: 0px 0px 1em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Why Abilene</h2><p dir=\"auto\" style=\"-webkit-text-stroke-width: 0px; border-color: var(--color-gray-200,currentColor); border-image: none; border-style: solid; border-width: 0px; box-sizing: border-box; caret-color: rgb(22, 21, 19); color: rgb(22, 21, 19); font-family: &quot;Oracle Sans&quot;, -apple-system, BlinkMacSystemFont, Inter, Vazirmatn, ui-sans-serif, system-ui, &quot;Segoe UI&quot;, Roboto, Ubuntu, Cantarell, &quot;Noto Sans&quot;, sans-serif, &quot;Helvetica Neue&quot;, Arial, &quot;Apple Color Emoji&quot;, &quot;Segoe UI Emoji&quot;, &quot;Segoe UI Symbol&quot;, &quot;Noto Color Emoji&quot;; font-size: 16px; font-style: normal; font-variant-caps: normal; font-weight: 400; letter-spacing: normal; margin-block: calc(var(--spacing)*0); margin: 0px 0px 1.25em; orphans: 2; padding: 0px; text-align: start; text-decoration-line: none; text-decoration-style: solid; text-decoration-thickness: auto; text-indent: 0px; text-transform: none; white-space: pre-line; widows: 2; word-spacing: 0px;\">Abilene is a key, growing location in Oracle’s data center footprint—offering meaningful ownership and visibility. You’ll lead critical infrastructure operations today while helping bring additional capacity online with the operational rigor required for world-class reliability. For CE leaders who want to build, improve, and operate at scale, Abilene offers an opportunity to make a measurable impact.</p>",
      "JobFunctionCode": "DCOPS",
      "HotJobFlag": true,
      "TrendingFlag": false,
      "BeFirstToApplyFlag": true,
      "WorkplaceTypeCode": null,
      "WorkplaceType": "",
      "OtherRequisitionTitle": null,
      "NumberOfOpenings": null,
      "HiringManager": null,
      "Organization": null,
      "BusinessUnitId": 300001096431127,
      "LegalEmployerId": 300000000482027,
      "OrganizationId": 300001902541887,
      "WorkerType": null,
      "JobType": null,
      "WorkMonths": null,
      "WorkYears": null,
      "WorkHours": null,
      "WorkDays": null,
      "LegalEmployer": null,
      "BusinessUnit": null,
      "Department": null,
      "DomesticTravelRequired": null,
      "JobFunction": "Data Center Operations",
      "secondaryLocations": [],
      "media": [],
      "workLocation": [],
      "otherWorkLocations": [],
      "requisitionFlexFields": [
        {
          "Prompt": "Role",
          "Value": "People Manager",
          "ControlType": "SingleChoiceList",
          "SequenceNumber": 4
        },
        {
          "Prompt": "Job Type",
          "Value": "Regular Employee",
          "ControlType": "SingleChoiceList",
          "SequenceNumber": 5
        }
      ],
      "primaryLocationCoordinates": [
        {
          "GeographyNodeId": 100257187669506,
          "GeographyId": 100000000729409,
          "Latitude": "32.44917",
          "Longitude": "-99.74142",
          "CountryCode": "US"
        }
      ],
      "skills": [
        {
          "SectionName": "Skills",
          "Skill": "Data Center Operations",
          "SkillType": null,
          "SkillId": "302964148525707",
          "SectionId": "300699536645979",
          "ContentItemId": null
        }
      ]
    }
  ],
  "count": 1,
  "hasMore": false,
  "limit": 499,
  "offset": 0,
  "links": [
    {
      "rel": "self",
      "href": "https://eeho.fa.us2.oraclecloud.com:443/hcmRestApi/resources/11.13.18.05/recruitingCEJobRequisitionDetails",
      "name": "recruitingCEJobRequisitionDetails",
      "kind": "collection"
    }
  ]
}
```
</details>





# BambooHR
**Pattern**: `https://{company}.bamboohr.com/careers/list`
* List: `result[]` summaries. Detail: `/careers/{id}/detail` → `result.jobOpening` with HTML `description`.
* Job URL: `result.jobOpening.jobOpeningShareUrl` = `https://{company}.bamboohr.com/careers/{id}`.

<details><summary>Sample Outputs</summary>

GET https://prentkeromich.bamboohr.com/careers/list (one element of result[])
---
```
{
  "id": "474",
  "jobOpeningName": "Shipping Clerk- Internal Only ",
  "departmentId": "18923",
  "departmentLabel": "Shipping",
  "employmentStatusLabel": "Full Time",
  "location": {
    "city": "Wooster",
    "state": "Ohio"
  },
  "atsLocation": {
    "country": null,
    "state": null,
    "province": null,
    "city": null
  },
  "isRemote": null,
  "locationType": "0"
}
```
GET https://prentkeromich.bamboohr.com/careers/474/detail (result.jobOpening)
---
```
{
  "jobOpeningShareUrl": "https://prentkeromich.bamboohr.com/careers/474",
  "jobOpeningName": "Shipping Clerk- Internal Only ",
  "jobOpeningStatus": "Open",
  "jobCategoryId": null,
  "departmentId": "18923",
  "departmentLabel": "Shipping",
  "employmentStatusLabel": "Full Time",
  "location": {
    "city": "Wooster",
    "state": "Ohio",
    "postalCode": "44691",
    "addressCountry": "United States"
  },
  "atsLocation": {
    "country": null,
    "countryId": null,
    "state": null,
    "city": null
  },
  "description": "<p><span style=\"font-weight: bold\">Keep Operations Moving – Join PRC-Saltillo as a Shipping Clerk</span></p>\n<p><br></p>\n<p>This is a hands-on role where accuracy, organization, and teamwork come together. Are you someone who likes staying busy, keeping things running smoothly, and making sure orders are accurate and on time? This position plays a key role in supporting daily operations across the company.</p>\n<p>The <span style=\"font-weight: bold\">Shipping Clerk</span> is responsible for processing and preparing outgoing shipments, ensuring orders are accurate, complete, and delivered on time. This role works closely with multiple departments, handles shipping systems and documentation, and helps maintain an organized, efficient shipping environment.</p>\n<p><span style=\"font-weight: bold\">Go to our website to apply</span> https://prentkeromich.bamboohr.com/careers</p>\n<p> </p>\n<p><span style=\"font-weight: bold\">Why You’ll Love Working Here</span><br></p>\n<p><span style=\"font-weight: bold\">Mission-Driven Work</span>: Support operations that help deliver communication solutions to those who need them.<br><span style=\"font-weight: bold\">Hands-On Role</span>: Stay active with a mix of shipping, inventory, and coordination tasks.<br><span style=\"font-weight: bold\">Team Environment</span>: Work closely with internal teams and external partners to keep things moving.<br><span style=\"font-weight: bold\">Growth Opportunities</span>: Build your skills with shipping systems, process improvements, and potential leadership responsibilities.</p>\n<p> </p>\n<p><span style=\"font-weight: bold\">Duties and Responsibilities</span></p>\n<ul>\n<li>Processes shipments using ERP and carrier systems, including creating labels and tracking deliveries.</li>\n<li>Prepares and packages orders, ensuring all items, accessories, and documentation are accurate and complete.</li>\n<li>Supports rush and priority shipments across multiple departments.</li>\n<li>Monitors finished goods to ensure quality and completeness before shipping.</li>\n<li>Assists with incoming deliveries and maintains accurate shipping records.</li>\n<li>Prepares documentation for domestic and international shipments.</li>\n<li>Maintains inventory of shipping supplies and performs regular inventory checks.</li>\n<li>Keeps the shipping and warehouse areas clean and organized.</li>\n<li>Provides high-quality service to both internal teams and external partners.</li>\n</ul>\n<p><br></p>\n<p><span style=\"font-weight: bold\">Education and Experience</span></p>\n<ul>\n<li>High school diploma or equivalent required<br><br></li>\n</ul>\n<p><span style=\"font-weight: bold\">Required Skills/Abilities</span></p>\n<ul>\n<li>Strong attention to detail, accuracy, and organization</li>\n<li>Ability to prioritize tasks and manage multiple responsibilities</li>\n<li>Team-oriented with strong communication skills</li>\n<li>Self-motivated and able to work with minimal supervision</li>\n<li>Adaptable and flexible in a fast-paced environment</li>\n<li>Basic computer skills (Clerk) / Proficient computer skills (Lead)</li>\n<li>Comfortable using equipment such as pallet jacks (training provided)</li>\n<li>Commitment to continuous improvement and quality</li>\n</ul>",
  "compensation": "$15 - $19 ",
  "datePosted": "2026-06-10",
  "minimumExperience": "Entry-level",
  "locationType": "0",
  "seekPromoted": false
}
```
</details>






# Breezy
**Pattern**: `https://{company}.breezy.hr/json?verbose=true`
* Returns an array of positions with full HTML `description` — `verbose=true` is required (without it, no description).
* Job URL: `url` = `https://{company}.breezy.hr/p/{friendly_id}`.

<details><summary>Sample Outputs</summary>

GET https://new-incentives.breezy.hr/json?verbose=true (one element)
---
```
{
  "id": "b8e6b722f7ed",
  "friendly_id": "b8e6b722f7ed-assistant-field-managers-external-vacancy",
  "name": "Assistant Field Managers -  (External Vacancy)",
  "url": "https://new-incentives.breezy.hr/p/b8e6b722f7ed-assistant-field-managers-external-vacancy",
  "published_date": "2026-05-25T14:45:23.799Z",
  "type": {
    "id": "fullTime",
    "name": "Full-Time"
  },
  "location": {
    "country": {
      "name": "Nigeria",
      "id": "NG"
    },
    "city": "Niger, Sokoto, Kebbi, Zamfara",
    "primary": true,
    "is_remote": false,
    "name": "Niger, Sokoto, Kebbi, Zamfara, NG",
    "id": "7ad8d074078b"
  },
  "department": "Operations",
  "salary": "",
  "company": {
    "name": "New Incentives",
    "logo_url": null,
    "friendly_id": "new-incentives",
    "isMultipleLocationsEnabled": false
  },
  "locations": [
    {
      "country": {
        "name": "Nigeria",
        "id": "NG"
      },
      "city": "Niger, Sokoto, Kebbi, Zamfara",
      "primary": true,
      "is_remote": false,
      "name": "Niger, Sokoto, Kebbi, Zamfara, NG",
      "id": "7ad8d074078b"
    }
  ],
  "description": "<h3><strong>Organization Overview</strong></h3>\n<p>NI-ABAE is an evidence-based organization that aims to provide incentives to increase childhood vaccination rates and save lives in the most cost-effective way. While caregivers and infants remain our top priority, our operations are guided by accountability, integrity, and a commitment to excellence.</p>\n<h3><strong><strong>Job Summary</strong></strong></h3>\n<p>New Incentives is looking to identify Assistant Field Managers that will help to expand its team dedicated to preventing child mortality in the NW/NC Zone.</p>\n<p><strong><u>This post is strictly for External candidates.</u></strong>&nbsp;<br></p>\n<p>The&nbsp;<strong>Assistant&nbsp;</strong><strong>Field Manager</strong>&nbsp;will support a New Incentives’ program that utilizes cash transfers to encourage mothers to complete the immunization schedule of their infants. The program is operated in cooperation with the State Primary Healthcare Development Board in NW &amp; NE.</p>\n<p>The primary duty of the Assistant Field Manager is to assist Field Managers in providing cash transfer disbursements at public clinics with the goal of increasing immunization coverage and retention. Key duties for this position include: high-quality operations of the program at selected clinics and providing leadership for selected staff members that result in increased immunization coverage and retention while nurturing relationships with employees, clinic staff, and other stakeholders.</p>\n<p>Assistant Field Managers are hired full-time and supervise Field Officers at clinics throughout our states of operations. The ideal candidate for this position is someone who is primarily residing in /or who has a free place to stay and willing to relocate to one of the towns/LGAs of all&nbsp;states of operation.</p>\n<p><strong><u>Context:</u></strong></p>\n<p>Over the past decade, Nigeria has considerably expanded the availability of vaccination services for infants. Despite this progress of vaccine supply, demand for vaccinations remains relatively low. New Incentives encourages caregivers to fully vaccinate their infants with cash transfers conditional on vaccinations, and thereby ensure their babies are protected against deadly diseases. The cash transfers allow the mothers to afford transport to the clinic and compensates them for lost income.<br></p>\n<p>Required Qualifications:</p>\n<ul><ul><ul><li>Preferably a candidate already based in <strong>Niger, Sokoto, Kebbi and or Zamfara&nbsp;State.</strong></li><li>Minimum of ND, preferably in the field of health or other natural sciences.</li><li>Willingness to work at remote public clinics and field locations at least 4 days per week.</li><li>Experience with the Nigerian health system and clinic documentation, particularly related to infant health and vaccinations.</li><li>Experience in collecting, synthesizing, and drawing conclusions from (health) data is desirable.</li><li>Careful attention to financial matters and management of funds.</li><li>Detail-oriented and diligent professional who is hard-working, result-oriented, and loyal.</li><li>Passion to help others and reduce infant mortality.</li><li>Very good verbal and written communication skills, in English, Hausa dialects (verbal and written)</li><li>Excellent responsiveness to email and phone requests.</li><li>Proficiency in Microsoft Word/Excel, internet browsers, smartphones, and new communications technology in general.</li><li>Motivation to work in a young organization that is constantly changing based on stakeholder feedback and operates with a lean structure.</li><li>Ability to demonstrate good managerial and leadership skills.</li></ul></ul></ul>\n<p>Language requirements:</p>\n<ul><li>Proficiency in English Language (conversation, reading and writing) is required</li><li><strong>Knowledge of Hausa, Nupe,&nbsp;and/or other commonly spoken Languages in the Niger State is required</strong></li></ul>\n<p><strong>How to Apply:</strong></p>\n<ul><li><strong>All submissions should be through breezy.hr by filling out this<strong>&nbsp;<a data-faitracker-click-bind=\"true\" href=\"https://docs.google.com/forms/d/e/1FAIpQLSc6lD-TjbN1GA8rRY59j8Zmsk-qCNlNV7cLOAA4ro91JudrTA/viewform?usp=publish-editor\">Google Form questionnaire</a></strong>. This position is OPEN and candidates will be considered on a rolling basis.</strong></li><li><strong><strong><em>Speaking local languages in the states where we are operating would be an added advantage.</em></strong></strong></li><li><strong>The deadline for receiving applications is 29th May 2026.</strong></li><li><strong>Only shortlisted candidates will be contacted.<br></strong></li></ul>\n<p><strong>Women and persons with disabilities are strongly encouraged to apply for this position!</strong></p>\n<p><em><u><strong>All internal applications and employee are strongly advised to use their official email address while applying.</strong></u></em><br></p>\n<p><em><strong>DISCLAIMER: Beware of fraudulent job offers. New Incentives – All Babies Are Equal Initiative does not charge a fee at any stage of the recruitment process.</strong></em></p>"
}
```
</details>


# Comeet
**Pattern**: `https://www.comeet.co/careers-api/2.0/company/{company_uid}/positions?token={token}&details=true`
* `company_uid` is in the careers URL `comeet.com/jobs/{name}/{company_uid}`; `token` is in the page source.
* Returns an array of positions; `details[]` holds the description sections.
* Job URL: `url_comeet_hosted_page` (`position_url` is the API detail, not for humans).

<details><summary>Sample Outputs</summary>

GET /careers-api/2.0/company/98.008/positions?token=…&details=true (TripleTen — one element, details[] trimmed to 2)
---
```json
{
  "name": "AI Automation Teaching Experts",
  "department": "Product Management",
  "email": "tripleten.AC.F59@comeetapply.com",
  "email_alias": null,
  "url_comeet_hosted_page": "https://www.comeet.com/jobs/tripleten/98.008/ai-automation-teaching-experts/AC.F59",
  "url_recruit_hosted_page": "https://www.comeet.com/jobs/tripleten/98.008/ai-automation-teaching-experts/AC.F59",
  "url_active_page": "https://www.comeet.com/jobs/tripleten/98.008/ai-automation-teaching-experts/AC.F59",
  "employment_type": "Hourly",
  "experience_level": "Senior",
  "uid": "AC.F59",
  "location": {
    "name": "Remote",
    "country": "US",
    "city": "Boston",
    "state": "",
    "postal_code": null,
    "street_name": "",
    "arrival_instructions": null,
    "street_number": null,
    "timezone": "America/Iqaluit",
    "location_uid": "30.40C",
    "is_remote": true
  },
  "internal_use_custom_id": null,
  "is_consent_needed": true,
  "is_sms_consent_needed": false,
  "referrals_reward": null,
  "is_reward": false,
  "is_company_reward": true,
  "company_referrals_reward": "",
  "url_detected_page": null,
  "picture_url": null,
  "time_updated": "2026-06-17T10:10:02Z",
  "company_name": "TripleTen",
  "is_internal": false,
  "linkedin_job_posting_id": null,
  "workplace_type": "Remote",
  "position_url": "https://www.comeet.co/careers-api/2.0/company/98.008/positions/AC.F59?token=89833903C282AF8044C011303C283C2819C8",
  "details": [
    {
      "name": "Description",
      "value": "<p><strong>Nebius Academy</strong> is an educational platform helping business professionals and operations teams stay ahead of the automation curve. We build specialized courses that teach practitioners how to design, implement, and scale AI-powered workflows using modern automation tools — from no-code platforms to enterprise-grade integrations.</p><p>Our programs are built for people who work in the real world: operations managers, business analysts, automation engineers, and digital transformation leads. We help them move beyond manual processes and become confident architects of intelligent workflows.</p><p>Our mission is to give professionals the knowledge and hands-on skills to automate smarter — and lead their organizations through the shift.</p><p><strong>Who are we looking for?</strong></p><p>We are building a talent pool of automation practitioners and business technology experts for ongoing roles as Instructors, Authors, and Subject Matter Experts in our AI Automation program.</p><p>We are looking for specialists across a range of seniority levels and focus areas:</p><ul><li><strong>Junior (Instructor / Technical Trainer)</strong> — 1–3 years designing or supporting automation workflows. Hands-on experience with tools like Zapier, Make, or n8n. Comfortable walking learners through workflows step-by-step with practical, repeatable examples.</li><li><strong>Mid (Author / Automation Engineer)</strong> — 3–6 years implementing automation across business operations. Experience with API integrations, prompt engineering, and RPA platforms. Able to explain automation concepts clearly and debug issues in real time.</li><li><strong>Senior (Subject Matter Expert / Automation Architect)</strong> — 6+ years in automation solution architecture, complex integrations, and large-scale project delivery. Experienced working with both technical teams and business stakeholders. Strong background in business analysis and digital transformation.</li></ul><p>A strong candidate doesn't just know automation tools — they use them daily and can teach others to do the same. We look for practitioners who have replaced real manual processes with working solutions, not just people who have studied the theory.</p><p>These are Talent Pool positions — we continuously review applications and build our roster of experts. There may not be an immediate opening at the time you apply, but strong candidates will be added to our talent pool and contacted as relevant opportunities arise.</p><p>You can join us on a part-time basis (10–15 hours per week) as an instructor leading live sessions and workshops, as a course author creating learning materials, or as a subject matter expert supporting curriculum development.</p><p><strong>Apply now</strong> — we review applications on an ongoing basis. Our selection process is fully asynchronous and designed to respect your time:</p><ol><li><strong>Application Review</strong> — we evaluate your profile against our current needs</li><li><strong>Async Video Interview</strong> — a short self-recorded interview (10–15 minutes max)</li><li><strong>Test Assignment</strong> — approximately 1 hour to complete</li><li><strong>Talent Pool</strong> — finalists are added to our active roster of vetted experts</li><li><strong>Hiring Manager &amp; Tech Expert Call</strong> — once a relevant position opens, we invite you to a live interview with our team</li><li><strong>Offer</strong> — we extend an offer for a relevant position upon successful completion of the process</li></ol><p><br></p><p><strong>Please submit your resume in English.</strong></p>",
      "order": 1
    },
    {
      "name": "What you will do",
      "value": "<p><strong>Available Roles</strong></p><p>We are building a talent pool of Instructors, Authors, and Subject Matter Experts for our AI Automation educational programs. We hire on an ongoing basis across the following specializations:</p><p>Most in demand: Make, Zapier, n8n, AI-powered workflow automation, API integrations Also relevant: Power Automate, UiPath, RPA platforms, prompt engineering for automation</p><p>Depending on your expertise, you may:</p><ul><li>Deliver live workshops and training sessions</li><li>Create educational content, assessments, and project-based learning materials</li><li>Design practical exercises, guides, prompts, and reference resources</li><li>Help define curriculum structure, learning objectives, and competency frameworks</li><li>Review content for technical accuracy and industry relevance</li><li>Share insights on AI tools, workflows, and emerging trends</li><li>Collaborate with instructional designers and curriculum teams to continuously improve learning outcomes</li><li>Support program updates based on learner feedback and market needs</li></ul><p>Areas of involvement</p><ul><li><strong>Instructor</strong> — leads live sessions, workshops, and learner interactions.</li><li><strong>Author</strong> — creates course content, lessons, assessments, and projects.</li><li><strong>Subject Matter Expert</strong> — shapes curriculum strategy, competency frameworks, and program direction.</li></ul><p>⌛ All roles are part-time: <strong>10–15 hours per week.</strong></p>",
      "order": 2
    }
  ],
  "categories": [
    {
      "name": "Brand",
      "value": "Nebius Academy",
      "order": 1
    }
  ]
}
```
</details>





# Teamtailor
**Pattern**: `https://{company}.teamtailor.com/jobs.rss` (or the site's custom careers domain + `/jobs.rss`)
* RSS/XML: per `<item>`, `<link>` is the job URL and `<description>` the full HTML detail; `tt:` tags add location/department.
* The JSON API (`api.teamtailor.com`) requires an API key.

<details><summary>Sample Outputs</summary>

GET https://career.teamtailor.com/jobs.rss (one item)
---
```
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="https://teamtailor.com/locations">
  <channel>
    <title>Teamtailor</title>
    <description>Current job openings</description>
    <link>https://career.teamtailor.com/jobs</link>
    <item>
      <title>Group Financial Controller</title>
      <description>&lt;h4&gt;Join Teamtailor and Help Shape the Future of Work!!&lt;br&gt;&lt;/h4&gt;&lt;p&gt;Teamtailor is a global Employer Branding and ATS SaaS platform used by over 13,000 companies, 250,000 users, and available in 90 countries worldwide. 🌍&lt;/p&gt;&lt;p&gt;&lt;span style="font-size:1em;font-weight:initial;letter-spacing:-0.011em;"&gt;Working at &lt;/span&gt;&lt;a target="_blank" href="https://www.teamtailor.com/en/"&gt;&lt;span style="font-size:1em;font-weight:initial;letter-spacing:-0.011em;"&gt;Teamtailor&lt;/span&gt;&lt;/a&gt;&lt;span style="font-size:1em;font-weight:initial;letter-spacing:-0.011em;"&gt; means being part of a dynamic, fast-paced tech company that values impact and responsibility. Our workplace fosters an environment where everyone can contribute meaningfully to the growth of the company. 🎉🥳&lt;/span&gt;&lt;/p&gt;&lt;p&gt;We are now looking for a Group Financial Controller to join our team, based in Stockholm&lt;/p&gt;&lt;p&gt;&lt;span&gt;The Group Financial Controller will own Group accounting, consolidation and statutory reporting across our international group of 10+ entities, with a Swedish parent company and subsidiaries across the US, UK, France, Germany and other markets.&lt;/span&gt;&lt;/p&gt;&lt;p&gt;&lt;span&gt;Since our transition to NetSuite in January 2026, a lot of how we work is still being defined - this is a chance to shape those processes early rather than step into a fixed structure&lt;/span&gt;&lt;/p&gt;&lt;p&gt;&lt;span&gt;This is a hands-on role: you will both prepare and own the consolidated financial statements, ensuring accuracy, compliance and strong financial governance. Alongside the core accounting mandate, you will play a key role in shaping how Finance works going forward - driving process efficiency and automation as we build on our new NetSuite infrastructure (live since January 2026).&lt;/span&gt;&lt;/p&gt;&lt;h4&gt;&lt;span&gt;Key responsibilities:&lt;/span&gt;&lt;/h4&gt;&lt;h5&gt;&lt;span&gt;Group Consolidation &amp;amp; Reporting&lt;/span&gt;&lt;/h5&gt;&lt;ul&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Own the end-to-end group consolidation process for 10+ entities, including intercompany accounting and eliminations, ensuring accurate and timely monthly, quarterly and annual closes.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Lead preparation of consolidated financial statements (P&amp;amp;L, balance sheet, cash flow) under IFRS across all business units and geographies.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Prepare the Group annual report in accordance with IFRS.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;/ul&gt;&lt;h5&gt;&lt;span&gt;Audit &amp;amp; Tax&lt;/span&gt;&lt;/h5&gt;&lt;ul&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Lead statutory reporting and audits, and own group tax compliance including corporate income tax, VAT, payroll taxes and transfer pricing governance, in collaboration with external advisors where needed.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;/ul&gt;&lt;h5&gt;&lt;span&gt;Internal Controls &amp;amp; Compliance&lt;/span&gt;&lt;/h5&gt;&lt;ul&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Design, implement and maintain the Group's financial governance and internal controls framework.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;/ul&gt;&lt;h5&gt;&lt;span&gt;Finance Transformation&lt;/span&gt;&lt;/h5&gt;&lt;ul&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Drive transformation initiatives that create efficiency and add value across the finance organization - championing the use of AI, process automation, reporting efficiency and data quality.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Act as a key finance stakeholder in shaping how NetSuite and other systems are used and developed, working closely with the team that owns system governance&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Partner closely with FP&amp;amp;A to ensure alignment between accounting outcomes and business performance, and collaborate cross-functionally with Legal, People and external advisors.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;/ul&gt;&lt;h4&gt;&lt;span&gt;What we are looking for:&lt;/span&gt;&lt;/h4&gt;&lt;ul&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Have several years of experience within group accounting, controllership or financial reporting&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Have experience preparing consolidated financial statements and annual reports under IFRS&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Are comfortable working in a multi-entity, multi-country environment&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Have experience managing external audits and tax filings across multiple jurisdictions&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Have strong communication skills in both Swedish and English&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Are highly organised and detail-oriented, comfortable operating in a fast-moving, ambiguous environment&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;/ul&gt;&lt;h4&gt;&lt;span&gt;What we offer:&lt;/span&gt;&lt;/h4&gt;&lt;ul&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;A high-impact role with significant ownership over the company's financial control environment and reporting cadence.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Competitive salary and benefits package.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Opportunity to work with a talented, international team and shape scalable accounting operations across multiple countries.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;li&gt;&lt;p&gt;&lt;span&gt;Professional development and career progression opportunities, plus regular socials and company-wide initiatives.&lt;/span&gt;&lt;/p&gt;&lt;/li&gt;&lt;/ul&gt;&lt;p&gt;If this role interests you please APPLY!&lt;/p&gt;&lt;figure data-alignment="left" class="relative" style="width:fit-content;margin-left:0px;margin-right:auto;"&gt;&lt;img src="https://images.teamtailor-cdn.com/images/s3/teamtailor-production/original-v3/image_uploads/e54f4356-afde-4ed2-acda-2cf1e369b392/original.jpeg" alt="Team picture on the beach" title="" data-alignment="left"&gt;&lt;/figure&gt;&lt;p&gt;&lt;/p&gt;</description>
      <pubDate>Fri, 24 Jul 2026 13:57:16 +0200</pubDate>
      <link>https://career.teamtailor.com/jobs/8124573-group-financial-controller</link>
      <remoteStatus>hybrid</remoteStatus>
      <guid>3ce2c88b-cbc6-4ae9-8ecb-000466c69037</guid>
      <tt:locations>
        <tt:location>
          <tt:name>Stockholm</tt:name>
          <tt:address>Östgötagatan 16</tt:address>
          <tt:zip>116 21</tt:zip>
          <tt:city>Stockholm</tt:city>
          <tt:country>Sweden</tt:country>
        </tt:location>
      </tt:locations>
      <tt:department>Finance</tt:department>
      <tt:role/>
      <tt:division/>
    </item>
    </channel>
</rss>
```
</details>
