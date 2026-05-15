> **Source PDF:** `references/pdfs/agent_first_data_systems_2025.pdf`
> Extracted with PyMuPDF. Equations and figures may be incomplete.

---

<!-- Page 1 -->
 Supporting Our AI Overlords:
 Redesigning Data Systems to be Agent-First

 Shu Liu, Soujanya Ponnapalli, Shreya Shankar, Sepanta Zeighami, Alan Zhu
 Shubham Agarwal, Ruiqi Chen, Samion Suwito, Shuo Yuan, Ion Stoica, Matei Zaharia
 Alvin Cheung, Natacha Crooks, Joseph E. Gonzalez, Aditya G. Parameswaran
 University of California, Berkeley

 Abstract of LLM agents tasked with finding reasons for why profits in coffee
 Large Language Model (LLM) agents, acting on their users’ behalf bean sales in Berkeley was low this year relative to last. Since they
 to manipulate and analyze data, are likely to become the dominant are not limited by human cognitive bandwidth and response times,
 workload for data systems in the future. When working with data, an army of agents could employ an enormous volume of queries to
 agents employ a high-throughput process of exploration and solu- data systems, far more than any human could—all for a single task.
 tion formulation for the given task, one we call agentic speculation. Many of these queries are likely wasteful, and are simply provid-2025 The sheer volume and inefficiencies of agentic speculation can pose ing the agents grounding. As another example, if an LLM agent is
 challenges for present-day data systems. We argue that data sys- tasked with identifying a new crew for a delayed flight, it would
 tems need to adapt to more natively support agentic workloads. We need to consider various hypothetical transactions to surface to
 a human decision maker, each with dozens of updates to variousDec take advantage of the characteristics of agentic speculation that we
 identify, i.e., scale, heterogeneity, redundancy, and steerability—to databases.2 For such tasks, agents may explore many alternatives in
6 outline a number of new research opportunities for a new agent- parallel by forking database state, running speculative updates, and
 first data systems architecture, ranging from new query interfaces, rolling back branches. Overall, as agentic workloads become more
 to new query processing techniques, to new agentic memory stores. and more prevalent, the sheer scale and inefficiencies of agentic
 speculation will become the bottleneck, and our data systems will
 1 Introduction need to evolve in response.
 So we ask the question: how can data systems evolve to better
 Powered by Large Language Models (LLMs) that can reason, invoke[cs.AI] support agentic workloads? In particular, can data systems natively—
 tools, author code, and communicate with each other, we are on the
 and efficiently—support agentic speculation, helping LLM agents
 precipice of a new agentic revolution that will transform how data
 determine the best course of action? This question—which, as we
 systems are used. Modern LLMs are far more efficient internally,
 argue, our community is well-equipped to answer—holds the key
 matching the capabilities of those orders of magnitude larger just a
 to unlocking unimaginable productivity gains from agents being
 year ago, and growing ever more effective at understanding and ma-
 the primary mechanism we use to interact with data.
 nipulating both structured and unstructured data. As they become
 Thankfully, while agentic speculation represents a new challenge
 both cheap and capable, future LLM agents will act on users’ behalf:
 for data systems, its characteristics present new opportunites for
 extracting, analyzing, transforming, and updating data—potentially
 the redesign of data systems. As we show, agentic speculation:
 becoming the dominant workload for data systems.
 While LLM agents may match human reasoning capabilities, (1) can be high throughput, benefiting from a lot of requests to the
 they won’t possess grounding—an awareness of the underlying backend systems, issued in sequence and/or in parallel, to determine
 data and characteristics of the data systems on which the data how to solve the given task.
 is stored. However, they can make up for this lack of grounding (2) is heterogeneous, spanning coarse-grained data and metadata ex-
 by tirelessly working through possible solutions to a given data ploration, partial and complete solution formulation, and validation—
 transformation task, far more than any human could or would. allowing LLM agents to make progress with approximate or incom-
 Each individual LLM agent can theoretically issue hundreds or plete outputs in early stages.arXiv:2509.00997v2 thousands of requests a second1, with this rate scaling with the (3) has redundancy: many requests may access similar data or per-
 number of LLM agents. Many of these requests are not attempts form overlapping operations, offering opportunities to share com-
 at a solution, but are instead part of an exploratory process of putation or eliminate redundant work.
 metadata discovery (e.g., table schemas, column statistics), coupled (4) is steerable: since speculation is fundamentally exploratory, if we
 with partial solutions and validation. We refer to this combination move beyond the query-answer paradigm and allow data systems
 of discovery and solution formulation as agentic speculation—i.e., to more directly communicate with LLM agents, it could help steer
 high-throughput, exploratory querying to identify the best course LLM requests toward the most promising directions.
 of action.
 In this paper, we propose a new research vision for our commu- Agentic speculation represents a substantial departure from
 nity around redesigning data systems for agents, by leveraging the present-day data systems workloads, which are either more in-
 aforementioned characteristics of speculation—scale, heterogene- termittent (e.g., from humans or tools operating on their behalf) or
 ity, redundancy, and steerability. In Sec. 2, we illustrate through more targeted (e.g., from end-user applications). Consider an army
 case studies the characteristics of present-day agentic speculation.
 1https://developer.nvidia.com/deep-learning-performance-training-inference/ai-
 inference 2Example thanks to Keshav Murthy at Couchbase.

<!-- Page 2 -->
 Liu et al.

 Activities across traces In Sec. 3, we propose a new architecture for agent-first data systems. 1 exploring tables In Sec. 4, 5, and 6, we identify new research opportunities in the exploring specific columns Frequency
 attempting part of the query
 interface, query processing, and storage layers, respectively. attempting entire query 0 Relative
 0.0 0.2 0.5 0.7 1.0
2 Case Studies Position in Trace Figure 3: Labeled agent activities, with x-axis showing nor-
 In this section we explore the characteristics of agentic workloads malized position in the trace, and each row (activity) normalthrough two case studies—and identify patterns in these queries ized independently. Agents first explore table and columns
 that present optimization opportunities. While these case studies then formulate queries, with phases often overlapping.
 are simple, they are easier to evaluate for correctness.
 70 Table 1: Mean activity counts per agent trace, averaged across
(%) (%)55 all traces, with and without human expert-provided hints.
Rate65 Rate50 Activity Avg (No Hints) Avg (w/ Hints) Reduction (%)
 60 45
 GPT-4o mini 40 GPT-4o mini exploring tables 3.44 2.95 -14.2
 exploring specific columns 3.56 2.57 -27.7Success55 Qwen2.5 Coder 7B Success35 Qwen2.5 Coder 7B attempting part of the query 4.28 2.71 -36.6
 10 20 30 40 50 1 2 3 4 5 6 7
 K Number of Turns attempting entire query 1.26 1.05 -16.6
 all SQL queries 12.67 10.38 -18.1
 (a) Success @ K (b) Success vs. Turns
 Figure 1: Results on the BIRD dataset Agentic speculation has substantial redundancy across re-
 We use the BIRD text2SQL benchmark [10] in our first study. We quests.
wanted to explore if present-day LLMs benefit from increasing the Across queries, the number of distinct sub-plans of each size is ofnumber of requests—in parallel or in sequence. We used DuckDB ten a small fraction of less than 10-20% of the total, representing
 as our backend, and GPT-4o-mini and Qwen2.5-Coder-7B-Instruct considerable potential for sharing computation.
 as two LLMs. To first evaluate parallel requests, we simulated the
behavior of an LLM agent “in charge,” with a number of “field” Our second case study is more involved than text2SQL and helps
agents each independently attempting the task, followed by the us study the phases of agentic speculation. We evaluate the peragent-in-charge picking one among the corresponding solutions. formance of a data agent that must combine information from
We plot the average success rate versus the number of LLM attempts two separate backend databases, chosen from PostgreSQL, SQLite,
 in Figure 1a. To instead evaluate sequential requests, we had a single MongoDB, and DuckDB. For example, one task involves cleaning
LLM agent issue queries until it was satisfied and once again plot customer information from MongoDB to join with user interaction
 the success rate versus the number of steps taken in Figure 1b. We data (e.g., upvotes) in DuckDB. As such, it is impossible to complete
 find that: this task in a single shot, and successful attempts typically involve
 interacting with both backends, followed by some computation in
 Agentic speculation—in sequence or in parallel—helps im- Python. We collected 44 sequential traces of OpenAI’s o3 model
 prove accuracy. attempting each of the 22 tasks twice, with about half resulting
 The success rate of agentic workloads increases as a function of in correct answers. We then manually labeled each action taken
 requests, and by 14%–70% in our case study. by the LLM with an annotation: exploring metadata and sample
 0.5 0.3 data (targeting schemas or with LIMIT), exploring column statis-
 1.5 0.4 (distinct values or aggregates), attempting part of the query,1000s) Total Total 1000s) Unique 1.0 Unique Unique Unique ticsof of 0.3 Prop. Prop. 0.2 or all of it. As we can see in the aggregated heatmap of traces in 1.0
(10s0.5 0.2 (10s 0.1 Figure 3, exploring metadata and sample data typically happens 0.5 0.1Proportion Proportion first, followed by statistics, after which the next two phases emerge.Count Count
 0.0 0.0 0.0 0.0 1 2 3 4 5 6 7+ PR TS FI HJ UA OT However, these phases are not clearly delineated, and each phase
 Subexpression Size Operator Types
 is present throughout the trace. So we find:
 (a) versus subexpression size. (b) versus root operation.
 Agentic speculation is heterogeneous in its informationFigure 2: Total vs. unique subexpressions (count and
 needs.proportion) across 50 attempts generated by GPT-4o-
 Requests from agents vary greatly in the information necessary,mini per problem, aggregated over the full BIRD dataset.
 from coarse-grained exploration of metadata and data statis-Here, PR=Projection, TS=Scan, FI=Filter, HJ=Hash Join,
 tics, to partial or more complete attempts at addressing the task.UA=Aggregate, OT=other operations.
 Coarse-grained, exploratory requests typically happen early on.
Next, we quantify the degree to which work sharing is possible
 across requests. We focus our attention on the parallel setting, In the following, we describe the earlier phases as metadata explowith 50 independent attempts—and evaluate the redundancy across ration, and the latter phases as solution formulation.
these attempts. We plot the total number and distinct number of Next, we wanted to explore if grounding provided by the backsub-plans or sub-expressions of each size in the 50 query plans end system could help reduce the number of steps taken to reach
generated for a given task, aggregated across the full BIRD dataset, the solution. So, we simulated this by measuring the impact of in-
 in Figure 2a. We present a similar plot for sub-plans grouped by jecting hints into the prompt, where the hint provides background
 root operator type in Figure 2b. We find: information useful for the task, such as which column contains

<!-- Page 3 -->
Supporting Our AI Overlords: Redesigning Data Systems to be Agent-First

information pertinent to the task. Again, we collected 44 sequential
traces (two per task) with hints provided, and then measured the
average number of steps required across attempts and tasks when
hints were provided versus not. As shown in Table 1, the impact of
hints is substantial. We find:

 Agentic speculation is steerable through grounding hints.
 Speculation traces can become much more efficient—reducing
 queries by >20%, depending on phase—if proactively provided
 grounding pertinent to the task.

 Based on the characteristics gleaned via our case studies, we
next propose a new architecture for agent-first data systems.

3 Agent-First Data System Architecture
 Figure 4: Agent-First Data Systems Architecture; componentsHere, we outline a potential architecture for a data system that is
 that are dashed involve LLM agents. Boxes in pink are coveredagent-first, as shown in Figure 4.
 in Sec. 4; blue in Sec. 5; orange in Sec. 6. Given a user task, an army of LLM agents can issue one or more
probes to the backend system, possibly associated with relative
priorities. We call these probes rather than queries for two rea- 4.1 From Agents to Data Systems
sons. First, they could go beyond SQL in providing background
 Probes from agents need to go beyond SQL in specifying why orinformation about the nature of the request, such as the phase
 how a given query needs to be answered. Moreover, for certain(metadata exploration or solution formulation), the identity of the
 types of information needs, SQL may be limiting, necessitating newagent issuing the request, the degree of accuracy required, overall
 operators. We describe each aspect in turn.goals, among others. We envision this information to be specified
in natural language or some other flexible format to be interpreted Providing Background Information. If all an agent can do is
by in-database agents. Second, the probes could go beyond SQL specify SQL queries, then all the data system can do is provide exact
on data or metadata (e.g., via information_schema) to search for results for those queries, making speculative probing inefficient.
tokens that may be present in any table (either column or row) to While specifying LIMIT or exact degree of approximation is one
help identify which tables need to be accessed. option, it provides limited expressive power. Therefore, as part of a
 Then, these probes are parsed and interpreted by an agentic probe, agents can specify one or more SQL queries, along with what
interpreter component within the database. For each of these probes, we call a brief: natural language statements about the probe’s goals
the system could provide answers, possibly approximate, and also and intents, its current phase (metadata exploration or solution
proactively provide information going beyond answers to help steer formulation), approximation needs and priorities across queries or
the agents through grounding feedback. We describe our interface probes, as well as any other open-ended information. These briefs
as well as proactive feedback in Sec. 4. are in turn examined by the probe interpreter agent within the data
 Given one or more probes, our probe optimizer attempts to system and used to guide optimization and execution, e.g., what
satisfice, i.e., produce reasonable results that address needs, with- order to execute the queries (if at all) and degree of approximation
out evaluating the query completely, as described in Sec. 5; this (or accuracy) based on goals and phase. Determining how to set
optimizer leverages and extends traditional database research on accuracy based on this natural language input is an open question
multi-query optimization and approximate query processing. and needs to also take into account relative query execution costs.
 To improve efficiencies, the storage and transactional compo- Across a batch of queries specified within a probe, the probe
nents of our data systems will need to evolve, as described in Sec. 6. can additionally specify open-ended goals that go beyond simple
We introduce an agentic memory store to store any grounding accuracy, such as pair-wise priorities, or indicating that only𝑘query
gleaned, so that they can be used in future probes. For updates, among 𝑛specific queries needs to be performed to completion (and
our shared transaction manager efficiently handles the sheer the data system can decide which one to maximize efficiency). For
redundancy in state involved across many potential transactions. example, if a field agent in an exploratory phase wants to get a
 sense for the differences in sales performance of stores on the US
 West coast vs. East coast, it can specify, as a part of the probe, that
 the data system needs to generate statistics for two states each from
4 Query Interfaces each coast, with the data system being able to pick which ones. The
In this section, we focus on agent-database interaction. We start by interface can furthermore allow for other forms of approximation
describing how probes (i.e., input from agents to the data system) indicators that are time-consuming for humans to write but can now
need to go beyond SQL in Sec. 4.1. Then, we discuss how data be done by agents, e.g., specifying termination criteria, functions
systems can go beyond the query-result paradigm in providing that the data system can evaluate on the partial result sets to know if
additional grounding information to help steer the agents in Sec. 4.2. some queries can be terminated early. For example, one termination

<!-- Page 4 -->
 Liu et al.

criteria could be defined to stop execution of multiple “needle-in- Cost Estimates and Cost-Based Feedback. Grounding can also
a-haystack” type queries mid-execution because the answers are come in the form of cost estimates; for example, even before exetoo similar to previous ones (where an agent defined function is cuting a query, estimated costs (especially if higher than expected)
evaluated on partial result sets to determine answer similarities). can be provided to the agent to help determine if the probe must
 be run to completion, and suggest the agent to modify the probe
Extending Capabilities through Flexible Probes. In many (e.g., to just focus on California instead of all of USA), or increase
cases, agents are unsure of even where to start and which tables the degree of approximation. This can similarly be applied across
to query for a given task—because they lack knowledge of how probes. For example, if the sleeper agent predicts that the probes
the data is organized. Suppose an agent is tasked with finding out are performing a set of tasks in sequence, it can suggest to the
how a given company will be “impacted by increased tariffs on the field agent to batch them, if it proves to be cheaper. The sleeper
import of electronic goods.” This agent may want to find tables agent can also take into account related materialized answers, or
whose name is semantically similar to “electronics,” or whose rows if a similar query was just answered for another agent. In such
contain data that is semantically similar. Such probes that ask for cases, the sleeper agent can suggest modifying the input probe to
semantically similar contents—be it tables, columns, or rows—to a probes with such pre-defined answers to improve efficiency—or it
specific phrase, located anywhere, are impossible to address within can output the answer for such related probes in the side-channel.
SQL, but are valuable during the early exploratory phases. Thus, we Next, we discuss how to efficiently provide answers to probes.
need native support for semantic similarity operators, beyond LIKE,
where the operators are applied to any data or metadata in the data 5 Processing and Optimizing Probes
system. Furthermore, as we will discuss in Sec. 6, the agents will
 As discussed in Sec. 2, agentic probes will have much higher throughrely on metadata stored in agentic memory, on cells, rows, columns,
 put than those issued by human sources (e.g., web applications).
and tables, typically written by agents themselves—to understand
 Importantly, in agent-first data systems, our goal is not to optimize
data semantics, and as such will need to frequently query or update
 overall throughput as in traditional databases, but to evaluate probes
this metadata. Although the above functionalities may be possible enough such that agents can make their decision on how to proceed in
through a combination of tools (e.g., store metadata separately in a
 the next turn. With that in mind, this section discusses what needs
vector database, look it up and then issue SQL queries), determining
 to change in data systems to effectively support probes.
what and how to actually store, and how to keep it up-to-date is
a challenge. Moreover, a data system that holistically supports all
 5.1 Supporting Exploration
data and metadata needs can be more effectively used by agents.
 Our agentic probes will consist of exploratory queries to estab-
 lish grounding. Some explorations will inevitably be cast in nat4.2 From Data Systems to Agents ural language (NL) as agents may lack knowledge about the unIn addition to simply answering probes, data systems should steer derlying databases (e.g., “how to find out how many tables are
agents towards better probes, which in turn can lead to improved stored?”) with others expressed using SQL (e.g., SELECT count(*)
efficiencies. In this way, the data system acts in a more proactive FROM information_schema.tables in PostgreSQL). Today’s databases
[20] manner, akin to how a data engineer or administrator may are not designed to answer NL queries. The probe optimizer in our
assist data analysts in satisfying information needs as efficiently agent-first data system must therefore orchestrate the mix of NL
as possible. This information can be provided in addition to, or in and SQL queries by utilizing different agents at the scale of probes.
lieu of the answers to the probes, in natural language. This steering To illustrate, consider identifying the stores that show an increascan serve two purposes: (1) helping agents by providing auxiliary ing sales trend. Our agents will first need to find out which tables
data-centric information the data system finds relevant, as a side- are used to store sales data. A straw person probe execution plan
channel, and (2) providing feedback to agents on efficiency and costs is to pose NL questions to a web search agent to discover how to
to assist the agents in designing their probes. We envision sleeper look up table schema for our specific database dialect, and execute
agents within the data system that are invoked on-demand to gather the found queries on our database. While these are simple queries
information in parallel with answering probes, to be returned in on our database’s metadata tables, the outputs returned from such
addition to probe answers, as we discuss below. queries often contain lots of unnecessary information. For instance,
 PostgreSQL maintains hundreds of internal tables and indexes even
Auxiliary Information. As we saw in Table 1, providing ground- without any user table defined. Coupled with the user tables, the
ing hints or feedback can reduce the number of probes agents need results can easily grow to thousands—or hundreds of thousands—
to complete their tasks. We envision sleeper agents tasked with of rows. Feeding all the rows to our query formulation agent is a
identifying and providing such hints as auxiliary information along waste of its limited context length. As mentioned in Section 4.1, we
with answers. For example, the sleeper agent could find and share further need the ability to query tokens regardless of where they
other related tables—to be either joined with (as in join discovery, appear across databases, be it as part of metadata or data.
e.g., [14]), or replacing the current table as the focus of analysis, Subsequently, to discover what constitutes an increasing trend,
especially if the current table proves irrelevant. Or rather than the one strategy is to find examples of “trend queries” (possibly using
agent having to guess why they got an empty result, the sleeper window queries) using NL with a web search agent, then feed the
agent can provide feedback reminiscent of why-not provenance [3], returned information to a query formulation agent to translate into
e.g., the probe assumed that states were encoded with two letter SQL. We will likely get lots of example queries online, and our dataacronyms like “CA”, but instead they are listed out in entirety. base will be bombarded with lots of inapplicable queries (e.g., they

<!-- Page 5 -->
Supporting Our AI Overlords: Redesigning Data Systems to be Agent-First

refer to non-existent tables, or identify the wrong trend). Worse yet, although the scale of queries to compute the differences will be
all such explorations will be mixed with other agents formulating much larger in agentic workloads. Finally, the database can take
solutions. With today’s data systems, we have no means to identify the agent’s phase into account; for example, return coarse grain
which queries are part of agentic exploration (and hence do not approximations during exploration, but more accurate answers
need to be evaluated completely). We envision that our probe opti- during solution formulation. Beyond pruning queries, we envision
mizer will prioritize queries based on their phases (i.e., a form of agents will be able to examine other internal database states (e.g.,
admission control). Furthermore, we will store previously gleaned buffer pool, outputs of query operators) to determine if it should
information using our agentic memory store to avoid repeated continue with query evaluation, or move on to the next turn.
querying of the same information, and train agents to query our Efficient Execution. As mentioned in Sec. 2, probes have substanmemory store instead of including such information as part of the tial redundancy that we can exploit by sharing computation across
prompt each time. them. Multi-query optimization [7, 13, 15], approximate query pro-
 cessing [6] and caching of partial query results can be used to
5.2 Probe Optimization improve efficiency. However, there are new unique challenges. For
As mentioned, probes issued by agents, unlike queries issued by example, different probes will have different approximation rehumans, do not require complete answers. The database interface quirements and may be accompanied with termination criteria (a
allows the agents to specify goals, and approximation needs in function that can be evaluated on partial results to know if they are
natural language via briefs, which are then used by the database sufficient, see Sec. 4), which makes it more difficult to reason about
to decide which probes to execute and to what degree of accuracy. their semantics and what can be shared. Moreover, the database can
This means the goal of the query optimizer, unlike in traditional incrementally evaluate queries, reminiscent of incremental query
data systems, is to decide both what queries to execute (and to what processing [2], but with the new challenge of decision making
degree of approximation) to satisfice for the probe, as well as how across them; e.g., the database must decide which probe is the most
to execute them. In doing so, the optimization has a new objective: useful to the agent and provide higher accuracy for that probe first
minimize the total time spent on answering the field agents’ probes before increasing accuracy for other probes. Finally, query plangiven available computational resources. Solving this optimization ning and processing can be done jointly with optimization, e.g., the
problem requires the database internally balancing cost/accuracy database can re-evaluate its decisions on what queries to run, or
trade-offs: if the database chooses to answer a query with high increase its level of approximation for some query during planning
degree of approximation providing insufficient answers to save or processing as it obtains more information.
cost upfront, the agent may ask many follow-ups with increased
 5.2.2 Inter-Probe Optimizations. The database can furthermore
accuracy requirements, thus increasing total time spent answering
 leverage the sequential interactions with agents across turns to furthe agent’s probes. We next discuss how we envision such an op-
 ther optimize both the queries it decides to run and their execution.
timization problem can be solved, within a given batch of probes
sampled at an interaction turn (Sec. 5.2.1), and across batches of Deciding What to Execute. Besides the strategies discussed in
probes across turns and agents (Sec. 5.2.2). Sec. 5.2.1, the database can consider all interactions with the agent
 to decide what queries to run. First, it can decide on queries to run
5.2.1 Intra-Probe Optimization. We first discuss how to optimize based on whether they provide any new information given past
a given batch of probes to provide sufficient information for the queries answered. For example, when given probes 𝑃and 𝑃′ by the
agent while minimizing computational cost. agents across consecutive turns, if the output between 𝑃and 𝑃′ is
 expected to not provide new information to the agent—e.g., 𝑃′ addsDeciding What to Execute. The database must first decide what
 new columns that are non-relevant to the agent’s goal—then 𝑃′ canqueries to run and to what degree of approximation, taking the
 be dropped. Furthermore, the database can decide what queries toprobe and its briefs into account. This requires the database to
 run in order to minimize the number of future follow-up probes.reason about the data and probe semantics, including the agent’s
 For example, based on the agent’s goal specified in the probe briefs,goals and phases. To do so, the database can use semantic query
 it can run a query it finds maximally useful to the agent exactlyand data understanding to check if they match user’s intents, and
 and to completion rather than approximately even if the currentprune away queries it deems not semantically meaningful. For
 query may take longer, expecting that the extra computation up-instance, during the exploration phase, the database can examine
 front will reduce total runtime across future interactions with thethe projected columns in probe to see if they are relevant to the
 agent. Yet another direction is to treat the problem as one of explo-user’s intent, and if not prune such columns, or the probes away as
 ration vs. exploitation: instead of always trying to provide rapida whole. Moreover, the database can compare probes within a batch,
 answers to queries by satisficing, the database can sometimes prior-guided by probe’s briefs that may have specified approximation
 itize exploration of underexplored solution spaces to identify thoseneeds across probes. The database can then make cost estimates and
 solutions that have an unanticipated benefit, in order to maximizecompare information gain from the probes to decide which probes
 utility over time.are more helpful and/or cheaper. For example, given two probes
𝑃and 𝑃′ the database can prune 𝑃′ away if rows that would be Efficient Execution. The database can decide to materialize and
returned by 𝑃′ −𝑃are deemed irrelevant to the agent’s goal. This is cache answers by observing the query history and considering the
reminiscent of prior work on pruning queries as part of visualization agent’s intent. For example, based on the history and the agent’s inrecommendation [17], and deciding query equivalence as part of tent, the database can expect future probes will continue to involve
query synthesis given user provided input/output examples [19, 21], the join of certain tables and can materialize the join.

<!-- Page 6 -->
 Liu et al.

6 Indexing, Storage, and Transactions underlying data or metadata, necessitating updates to any related inThe heterogeneity and redundancy of agentic speculation work- formation in the agentic memory. For example, if there is a schema
loads fundamentally challenge the assumptions of the storage layer update, the results of a prior probe that used that table may no
of today’s data stores, specifically, that workloads are static and longer be relevant. One approach is to allow this memory store be
independent. inconsistent with the data/metadata, and instead be updated by any
 For static workloads, data systems rely on predefined indexes new probes that discover that the information is stale. However,
and fixed storage layouts (e.g., column-based for OLAP) based on re- the downside is that the stale information may lead a new probe
curring workload patterns. Agentic probes, by contrast, evolve from to make a mistake. For example, suppose the agentic memory incoarse-grained metadata exploration to final validation. This dy- dicates that the only relevant sales information can be found in
namism makes static tuning ineffective. Meanwhile, the exploratory three tables, but after that, additional relevant tables were added;
(resp. solution formulation) phases of different probes may be simi- here, new probes may end up returning incorrect results. Additional
lar and can benefit from similar layouts. challenges emerge in supporting access control for multiple users.
 On the independence front, data systems treat queries as unre- For example, agents acting on different users’ behalf could ask simlated, such that concurrent access (specifically writes) from these ilar questions (e.g., "Where is the employee’s availability stored?").
queries must be isolated from each other. While this simplifies ap- Sharing answers across such agents boosts efficiency—but raises
plication logic and ensures consistency, these mechanisms prevent privacy concerns, especially in the aggregate [12]. Addressing these
cooperative sharing of state with rare exceptions [8]. Instead of challenges will need to draw inspiration from work on knowledge
isolation, agentic workloads demand a more cooperative model— bases as well as schema evolution.
one that can safely share intermediate state across different probes,
many of which are likely to be similar. 6.2 Performing Branched Updates
 Hence, we propose two key ideas to improve performance. First, When transforming or updating data, agents typically explore mulwe propose an agentic memory store that acts as a “pseudo-index” tiple “what-if” hypotheses, i.e., branches. For example, at Neon [1],
to help agentic probes quickly find information that may be helpful, we observed that agents created 20× more branches, and performed
either directly accessed by them, or on their behalf by sleeper agents. 50× more rollbacks, relative to humans. Traditional transactional
Second, we propose a new transactions framework that is centered guarantees instead operate within a linear thread of execution. Here,
on state sharing across probes, each of which may be independently with agentic speculation, we instead want multi-world isolation,
attempting to complete a user-defined sequence of updates. where each branch must be logically isolated, but may physically
 overlap.
6.1 Agentic Memory Store Branch Isolation. Existing models of branching consistency, deThe exploratory phase of agentic speculation aims to identify the veloped in the weak consistency era, e.g., in Bayou, Dynamo, or
right tables and columns to operate on. To improve efficiency, data Tardis [4, 5, 11], as well as versioned databases [9] can offer inspisystems should maintain a persistent, queryable agentic memory ration. However, agentic speculation goes further: multiple agents
store—a semantic cache that provides grounding. may create forks that must eventually reconcile—not just with the
 mainline, but with each other. This requires new models of multi-Artifacts. The first question is what should be stored. One idea
 agent, multi-version isolation. Most branches will be similar—e.g.,is to store the results of prior probes and partial solutions, so that
 same schema, 90% identical data—but isolation requires that theiragents can reuse what is known about the data and metadata, en-
 effects remain logically separate.abling similar probes to be more efficient. In addition, we can store
information about the data and metadata, possibly associated with Efficient forking and rollbacks. Naively duplicating entire datathe tables themselves. We can store encoding formats for columns, bases per branch is prohibitively expensive and inefficient, making
missing value information, and time and location granularities. For support for efficient forking crucial. Industrial systems like Neon [1],
example, an agent trying to explore various sales partitions may Aurora [18], and Bauplan [16] and academic systems like Tardis [4]
retrieve a number of them, along with the metadata in the agentic adopt copy-on-write approaches to lazily clone state. However,
memory that indicates the date ranges or location ranges associ- these are still far from what is needed for agentic speculation at
ated with each—so that it can make a more informed decision about a massive scale. We need new concurrency mechanisms that exwhich ones to probe further. ploit similarity across branches and preserve logical isolation (no
 To implement this store, we can embed the agentic metadata cross-contamination), to enable massive parallel forking. This is
with the table directly, to be retrieved if the table is queried. For analogous to MVCC on steroids: forking possibly thousands of
all other open-ended information, one approach is to use a vector near-identical snapshots and rolling back all but one. Unlike tradiindex to support semantic similarity search on embeddings (e.g., tional data systems, where rollbacks are rare, we require ultra-fast
querying with a probe might retrieve other similar probes, and rollbacks (i.e., fast aborts for failed branches).
what worked for them). However, this approach may not work as
well for more targeted or more structured lookups. 7 Conclusion
Updates to the Store. A separate concern is how this memory We described our vision for data systems that natively support
store is maintained during updates. Updates could be in the form emerging agentic workloads. These workloads involve agentic specof new probes being executed that may provide new information ulation, characterized by a high-throughput, heterogeneous, and
that augments or supersedes existing ones. Or, it could be to the redundant mix of discovery and validation, specified by probes

<!-- Page 7 -->
Supporting Our AI Overlords: Redesigning Data Systems to be Agent-First

ideally involving a combination of queries and natural language. and Werner Vogels. 2007. Dynamo: Amazon’s highly available key-value store.
We present one such architecture for such redesigned data systems, ACM SIGOPS operating systems review 41, 6 (2007), 205–220.
 [6] Minos N Garofalakis and Phillip B Gibbons. 2001. Approximate Query Processing:
and discuss emergent research challenges. Taming the TeraBytes. 10 (2001), 645927–672356.
 [7] Georgios Giannikis, Gustavo Alonso, and Donald Kossmann. 2012. SharedDB:
Acknowledgments killing one thousand queries with one stone. Proceedings of the VLDB Endowment
 5, 6 (2012), 526–537.
This work is supported by NSF grants IIS-1955488, IIS-2027575, [8] Nitin Gupta, Milos Nikolic, Sudip Roy, Gabriel Bender, Lucja Kot, Johannes
DGE-2243822, IIS-2129008, IIS-1940759, and IIS-1940757, DOE awards Gehrke, and Christoph Koch. 2011. Entangled transactions. Proceedings of the VLDB Endowment 4, 11 (2011), 887–898.
DE-SC0016260, AC02-05CH11231, DARPA agreement HR00112- [9] Silu Huang, Liqi Xu, Jialin Liu, Aaron J Elmore, and Aditya Parameswaran. 2017.
590131, and funds from the state of California. This work is also ORPHEUSDB: Bolt-on Versioning for Relational Databases. Proceedings of the
 VLDB Endowment 10, 10 (2017).
supported by EPIC Data Lab sponsors and affiliates, including [10] Jinyang Li et al. 2023. Can LLM Already Serve as A Database Interface? A BIg
Adobe, Bridgewater, Google, G-Research, Microsoft, PromptQL, Bench for Large-Scale Database Grounded Text-to-SQLs. NeurIPS (2023).
Sigma Computing, and Snowflake, as well as Berkeley Sky Lab spon- [11] Karin Petersen, Mike Spreitzer, Douglas Terry, and Marvin Theimer. 1996. Bayou:
 replicated database services for world-wide applications. (1996), 275–280.
sors and affiliates, including Accenture, AMD, Anyscale, Broadcom, [12] Raluca Ada Popa et al. 2011. CryptDB: Protecting confidentiality with encrypted
Google, IBM, Intel, Intesa Sanpaolo, Lambda, Lightspeed, Mibura, query processing. In Proceedings of the 23rd ACM SOSP. 85–100.
Samsung SDS, SAP, Cisco, Microsoft and NVIDIA. Compute credits [13] Prasan Roy, Srinivasan Seshadri, S Sudarshan, and Siddhesh Bhobe. 2000. Efficient and extensible algorithms for multi query optimization. In Proceedings of the
were provided by Azure, Modal, NSF (via NAIRR), and OpenAI. We 2000 ACM SIGMOD international conference on Management of data. 249–260.
thank the reviewers for their feedback, as well as Arash Nourian [14] Anish Das Sarma, Lujun Fang, Nitin Gupta, Alon Y Halevy, Hongrae Lee, Fei
 Wu, Reynold Xin, and Cong Yu. 2012. Finding related tables. 10 (2012), 2213836–
for helpful discussions. 2213962.
 [15] Timos K Sellis. 1988. Multiple-query optimization. TODS (1988).
References [16] Jacopo Tagliabue and Ciro Greco. 2025. Safe, Untrusted," Proof-Carrying" AI
 Agents: toward the agentic lakehouse. arXiv preprint arXiv:2510.09567 (2025).
 [1] Neon Team at Databricks. 2025. Neon Severless Postgres. https://neon.com/ [17] Manasi Vartak, Sajjadur Rahman, Samuel Madden, Aditya Parameswaran, and
 [2] Badrish Chandramouli, Jonathan Goldstein, Mike Barnett, Robert DeLine, Danyel Neoklis Polyzotis. 2015. Seedb: Efficient data-driven visualization recommenda-
 Fisher, John C Platt, James F Terwilliger, and John Wernsing. 2014. Trill: A high- tions to support visual analytics. In Proceedings of the VLDB Endowment Interna-
 performance incremental query processor for diverse analytics. Proceedings of tional Conference on Very Large Data Bases, Vol. 8. 2182.
 the VLDB Endowment 8, 4 (2014), 401–412. [18] Alexandre Verbitski, Anurag Gupta, Debanjan Saha, Murali Brahmadesam,
 [3] James Cheney, Laura Chiticariu, Wang-Chiew Tan, et al. 2009. Provenance in Kamal Gupta, Raman Mittal, Sailesh Krishnamurthy, Sandor Maurice, Tengiz
 databases: Why, how, and where. Foundations and Trends® in Databases 1, 4 Kharatishvili, and Xiaofeng Bao. 2017. Amazon aurora: Design considerations
 (2009), 379–474. for high throughput cloud-native relational databases. (2017), 1041–1052.
 [4] Natacha Crooks, Youer Pu, Nancy Estrada, Trinabh Gupta, Lorenzo Alvisi, and [19] Chenglong Wang, Alvin Cheung, and Rastislav Bodik. 2017. Interactive query
 Allen Clement. 2016. Tardis: A branch-and-merge approach to weak consistency. synthesis from input-output examples. (2017), 1631–1634.
 (2016), 1615–1628. [20] Sepanta Zeighami et al. 2025. LLM-Powered Proactive Data Systems. IEEE Data
 [5] Giuseppe DeCandia, Deniz Hastorun, Madan Jampani, Gunavardhan Kakulapati, Eng. Bulletin March 2025 (2025).
 Avinash Lakshman, Alex Pilchin, Swaminathan Sivasubramanian, Peter Vosshall, [21] Moshé M. Zloof. 1975. Query-by-Example: the Invocation and Definition of
 Tables and Forms. VLDB (1975).