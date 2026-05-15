> **Source PDF:** llmlingua_emnlp2023.pdf (arXiv:2310.05736)
> Extracted with PyMuPDF.

---

<!-- Page 1 -->
 LLMLingua: Compressing Prompts for Accelerated Inference
 of Large Language Models

 Huiqiang Jiang, Qianhui Wu, Chin-Yew Lin, Yuqing Yang, Lili Qiu
 Microsoft Corporation
 {hjiang, qianhuiwu, cyl, yuqing.yang, liliqiu}@microsoft.com

 Abstract LLMs through quantization (Dettmers et al., 2022;
 Xiao et al., 2023), compression (Frantar and Alis-
 Large language models (LLMs) have been ap-
 tarh, 2023), etc. However, these approaches may plied in various applications due to their as-
 be not suitable when the LLMs can be accessed via tonishing capabilities. With advancements in
 technologies such as chain-of-thought (CoT) APIs only.
 prompting and in-context learning (ICL), the Approaches that attempt to reduce the length of2023 prompts fed to LLMs are becoming increas- original prompts while preserving essential infor-
 ingly lengthy, even exceeding tens of thousands mation have emerged lately. These approaches are
 of tokens. To accelerate model inference and
 grounded in the concept that natural language isDec reduce cost, this paper presents LLMLingua,
 inherently redundant (Shannon, 1951) and thus can a coarse-to-fine prompt compression method6
 that involves a budget controller to maintain be compressed. Gilbert et al. (2023) also indicate
 semantic integrity under high compression ra- that LLMs can effectively reconstruct source code
 tios, a token-level iterative compression algo- from compressed text descriptions while maintain-
 rithm to better model the interdependence be- ing a high level of functional accuracy. Therefore,
 tween compressed contents, and an instruc- we follow this line of studies to compress a long
 tion tuning based method for distribution align-[cs.CL] prompt into a shorter one without any gradient flow
 ment between language models. We conduct
 through the LLMs to support applications based on experiments and analysis over four datasets
 from different scenarios, i.e., GSM8K, BBH, a larger range of LLMs.
 ShareGPT, and Arxiv-March23; showing that In terms of information entropy, tokens with
 the proposed approach yields state-of-the-art lower perplexity (PPL) contribute less to the over-
 performance and allows for up to 20x compres- all entropy gains of the language model. In other
 sion with little performance loss.1
 words, removing tokens with lower perplexity has
 1 Introduction a relatively minor impact on the LLM’s compre-
 hension of the context. Motivated by this, Li
 The widespread adoption of ChatGPT has trans- (2023) propose Selective-Context, which first em-
 formed numerous scenarios by harnessing the pow- ploys a small language model to compute the self-
 erful generalization and reasoning capabilities of information of each lexical unit (such as sentences,
 large language models (LLMs). In practical ap- phrases, or tokens) in original prompts, and thenarXiv:2310.05736v2 plications, crafting suitable prompts is crucial drops the less informative content for prompt com-
 and usually involves techniques such as chain-of- pression. However, this method not only ignores
 thought, in-context learning, and retrieving related the interdependence between the compressed con-
 documents or historical conversations (Wei et al., tents but also neglects the correspondence between
 2022; Chase, 2022). While these methods can the LLM being targeted and the small language
 elicit highly effective generations by activating model used for prompt compression.
 LLMs’ domain-specific knowledge, they often re- This paper proposes LLMLingua, a coarse-
 quire longer prompts. Therefore, striking a balance to-fine prompt compression method, to address
 between the massive computational demands of the aforementioned issues. Specifically, we first
 LLMs and the need for longer prompts has become present a budget controller to dynamically allo-
 an urgent issue. Some studies attempt to accelerate cate different compression ratios to various com-
 model inference by modifying the parameters of ponents in original prompts such as the instruction,
 1Our code is available at https://aka.ms/LLMLingua. demonstrations, and the question, and meanwhile,

<!-- Page 2 -->
perform coarse-grained, demonstration-level com- Chevalier et al., 2023) even require to fine-tune the
pression to maintain semantic integrity under high whole language model, which severely limits their
compression ratios. We further introduce a token- application scenarios. Furthermore, there are some
level iterative algorithm for fine-grained prompt studies (Chase, 2022; Zhang et al., 2023) that atcompression. Compared with Selective Context, it tempt to utilize LLMs to summarize dialog or data,
can better preserve the key information within the thereby forming memory and knowledge. Howprompt by taking into account the conditional de- ever, these approaches require multiple invocations
pendencies between tokens. Additionally, we pose of LLMs, which are quite costly.
the challenge of distribution discrepancy between Some methods reduce the prompt length by sethe target LLM and the small language model used lecting a subset of demonstrations. For example,
for prompt compression, and further propose an Zhou et al. (2023) introduces a reinforcement learninstruction tuning based method to align the distri- ing based algorithm to allocate a specific number
bution of both language models. of demonstrations for each question. Some other
 We validate the effectiveness of our approach on methods focus on token pruning (Goyal et al., 2020;
four datasets from different domains, i.e., GSM8K Kim and Cho, 2021; Kim et al., 2022; Rao et al.,
and BBH for reasoning and ICL, ShareGPT for con- 2021; Modarressi et al., 2022) and token mergversation, and Arxiv-March23 for summarization. ing (Bolya et al., 2023). However, these approaches
The results show that our method yields state-of- are proposed for smaller models such as BERT, ViT.
the-art performance across the board. Furthermore, Moreover, they depend on fine-tuning the models
we conduct extensive experiments and discussions or obtaining intermediate results during inference.
to analyze why our approach attains superior per- The most similar work to this paper is Selectiveformance. To our best knowledge, we are the first Context (Li, 2023), which evaluates the inforto evaluate reasoning and ICL capabilities in the mativeness of lexical units by computing selfdomain of efficient LLMs. information with a small language model, and
 drops the less informative content for prompt com2 Related Work pression. This paper is inspired by Selective-
 Context and further proposes a coarse-to-fine
2.1 Efficient LLMs
 framework to address its limitations.
Efficient large language models have gained significant attention in recent research community, 2.2 Out-of-Distribution (OoD) Detection
especially with the growing prominence of Chat- Recently, a series of studies have been proposed
GPT. Most of these methods aim to reduce the for unsupervised OoD detection. With only incosts of inference and fine-tuning by modifying the distribution texts available for learning, these methmodel parameters through quantization (Dettmers ods either fine-tune a pre-trained language model
et al., 2022; Frantar et al., 2023; Xiao et al., 2023), (Arora et al., 2021) or train a language model from
compression (Frantar and Alistarh, 2023), instruct scratch (Mai et al., 2022). Wu et al. (2023) analyze
tuning (Taori et al., 2023; Chiang et al., 2023; Xu the characteristics of these methods and leverage
et al., 2023), or delta tuning (Hu et al., 2022). multi-level knowledge distillation to integrate their
 A line of studies attempt to optimize inference strengths while mitigating their limitations. Finally,
costs from the perspective of the input prompts. perplexity output by the resulting language model
Motivated by the observation of the abundance of is used as the indication of an example being OoD.
identical text spans between the input and the gener- This paper also regards perplexity as a measureated result, Yang et al. (2023) directly copy tokens ment of how well a language model predicts a samfrom prompts for decoding to accelerate the infer- ple. In contrast to out-of-distribution detection,
ence process of LLMs. Some approaches focus on which identifies examples with high perplexities
compressing prompts, specifically, learning special as indicative of unreliable predictions, we consider
tokens via prompt tuning of LLMs to reduce the tokens with higher perplexity to be more influential
number of tokens to be processed during inference during the inference process of language models.
(Mu et al., 2023; Ge et al., 2022; Wingate et al.,
 2.3 LLMs as a Compressor2022; Chevalier et al., 2023; Ge et al., 2023). Unfortunately, these methods are usually tailored to Recently, many perspectives have interpreted
particular tasks and some of them (Mu et al., 2023; large language models and unsupervised learn-

<!-- Page 3 -->
 OriginalOriginal PromptPrompt LLMLinguaLLMLingua Black-boxBlack-box LLMsLLMs

 Instruction:Instruction: FollowFollow thethe givengiven examplesexamples
 andand answeranswer thethe question.question. II BudgetBudget DemonstrationDemonstration 1:1: Q:Q: InIn aa certaincertain
 school,school, 2/32/3 ofof thethe malemale studentsstudents likelike toto ControllerController
 playplay basketball,basketball, ….…. WhatWhat percentpercent ofof thethe
 populationpopulation ofof thethe schoolschool dodo notnot likelike toto
 playplay basketballbasketball ifif thethe ratioratio ofof thethe malemale toto 00 DistributionDistribution IIIIII CompressedCompressed femalefemale studentsstudents isis 3:23:2 andand therethere areare AlignmentAlignment PromptPrompt ExecutionExecution 10001000 students?students? Let'sLet's thinkthink stepstep byby stepstep
 TheThe studentsstudents areare divideddivided intointo 33 ++ 22 == 55
 EachEach partpart representsrepresents 1000/51000/5 == 200200
 students.students. So,So, therethere areare 33 xx 200200 == 600600 SmallSmall CompressedCompressed PromptPrompt males.males. AndAnd therethere areare 22 xx 200200 == 400.400.
 …basketball…basketball isis 520/1000520/1000 ** 100100 == 52.52. ModelModel :: SamSam boughtbought aa dozendozen boxesboxes eacheach 3030
 TheThe answeranswer isis 52.52. highlhighl penspens inside,inside, $10$10 each.each. ...... theltersthelters
 DemonstrationDemonstration 2:2: separatelyseparately atat thethe ofof threethree $2.$2. muchmuch makemake
 …… total,\nLetstotal,\nLets thinkthink step\nboughtstep\nbought boxesboxes x0x0
 DemonstrationDemonstration 8:8: Q:Q: SamSam boughtbought aa oflters\nHeoflters\nHe 22 3ters3ters in\nSamin\nSam thenthen boxesboxes
 dozendozen boxes,boxes, eacheach withwith 3030 highlighterhighlighter 6lters/box6lters/box 0ters\nHe0ters\nHe soldsold thesethese boxesboxes
 penspens inside,…inside,… TheThe answeranswer isis 115.115. IIII IterativeIterative Token-Token- 5\nAfterelling5\nAfterelling thesethese boxesboxes therethere
 Question:Question: Janet’sJanet’s ducksducks laylay 1616 eggseggs perper LevelLevel PromptPrompt 36030lters\nese0036030lters\nese00 ofof three\nsoldthree\nsold groups2groups2
 day…..day….. HowHow muchmuch inin dollarsdollars doesdoes sheshe CompressionCompression eacheach soso mademade *2*2 $20$20 from\nInfrom\nIn total,total,
 makemake everyevery dayday atat thethe farmers'farmers' market?market? he015\nSincehe015\nSince hishis hehe $$ -- $120$120 == $115$115 inin
 profit.\nTheprofit.\nThe answeranswer isis 115115
 23662366 tokenstokens 117117 tokenstokens

 Figure 1: Framework of the proposed approach LLMLingua.

ing as a kind of compressor for world knowl- 4 Methodology
edge (Sutskever, 2023; Delétang et al., 2023), by
 In this section, we elaborate on the proposed coarse-using arithmetic coding (Rissanen, 1976; Pasco,
 to-fine prompt compression approach, LLMLingua.1976). Our research can be viewed as an endeavor
 First, we introduce a budget controller to dynam-to further compress information within prompts by
 ically allocate different compression ratios to var-capitalizing on the compression-like characteristics
 ious components in prompts and meanwhile, per-of large language models.
 form coarse-grained, demonstration-level compres-
 sion to maintain semantic integrity under high com3 Problem Formulation
 pression ratios. Next, we describe the proposed it-
 erative prompt algorithm designed to retain knowl-A prompt compression system is designed to gen-
 edge from the prompt while compressing. Finally,
erate a compressed prompt = from 𝑖=1 ex {e𝑥𝑖}e𝐿 we introduce alignment to address the distributiona given original prompt x = (xins, xdems, xque),
 gap between the small model and black-box large
 , andwhere xins = {𝑥ins𝑖}𝐿ins𝑖=1 , xdems = {𝑥dems𝑖 }𝐿dems𝑖=1 models. Figure 1 show the framework.
 denote the instruction, demon-xque = {𝑥que𝑖 }𝐿que𝑖=1
strations, and the question in the original prompt 4.1 Budget Controller
x. e𝐿, 𝐿ins, 𝐿dems, and 𝐿que represent the numbers The budget controller here is designed to allocateof tokens xins, xdems, and xque, respectively. in ex, different budgets, i.e., compression ratio, to differ- +Let denote the total se- 𝐿= 𝐿ins 𝐿dems + 𝐿que
 ent components in a prompt such as instructions,quence length of x, the compression rate is defined
as 𝜏= e𝐿/𝐿, 𝜏∈[0, 1], and the compression ratio demonstrations, and questions, at the sentence or demonstration level. There are two considerations:is 1/𝜏. A smaller value of 𝜏implies a lower inference cost, which is preferable. Let ex𝐺represent (i) In general, the instruction and the question in
the LLM-generated results derived by ex and x𝐺 a prompt have a direct influence on the generateddenotes the tokens derived by x, the distribution of results, as they should contain all the necessary
ex𝐺is expected to be as similar to x𝐺as possible. knowledge to generate the following answer. OnThis can be formulated as: the contrary, if there are multiple demonstrations
 in the original prompt, the conveyed information
 may be redundant. Therefore, a tailored budget
 min 𝑃(x𝐺|x)), (1) controller is required to allocate more budget (i.e., ex,𝜏KL(𝑃(ex𝐺|ex),

<!-- Page 4 -->
Algorithm 1 Pseudo code of Budget Controller. Algorithm 2 Pseudo code of Iterative Token-level
Input: A small language model M𝑠; the original prompt Prompt Compression (ITPC).
x = (xins, xdems, xque). Input: A small language model M𝑠; the prompt from budget
 1: Set the selected demonstration set D = 𝜙. controller x′ = (xins, xD, xque); target compression rate𝜏,
 2: Get demonstration compression rate 𝜏dem by Eq.(2). adjusted compression rate △𝜏ins,que.
 3: Calculate the perplexity of each demonstration via M𝑠. 1: Set the selected token set T = 𝜙
 4: Rank all demonstrations in descending order of their per- 2: Get segment set S.
 plexity as a list (xdem , ..., xdem(𝑁)), where 𝑁is the number 3: for 𝑖= 1, 2, . . . , 𝑚do (1)
 of demonstrations, xdem is the 𝑖-th demonstration. 4: Get the conditional probabilities 𝑝(s𝑖) via Eq.(5) (𝑖) 5: Get the compression threshold 𝛾𝑖with Eq. (6). 5: for 𝑖= 1 do
 6: if e𝐿D > 𝑘· 𝜏dems𝐿dems then 6:7: endAppendfor the compressed token to T via Eq.(7).
 7: Break.
 8: Concatenate all tokens in T as 8: end if ex.
 9: Append xdem to D. Output: The compressed prompt ex. (𝑖)
10: 𝑖= 𝑖+ 1
11: end for
12: Allocate remaining budget to xins and xque via Eq. (3). demonstration to D will make the total number of
Output: The subset of demonstrations D obtained from tokens in D exceed maximum tokens 𝑘·𝜏dems𝐿dems,
coarse-grained compression; Additional budget Δ𝜏ins,que for where 𝑘is the granular control coefficient.the instruction and the question.

 Adjust compression ratios for instruction and
 question. After obtaining the coarse-grainedsmaller compression ratios) for instructions and
 we allocate the re-questions, and less budget for demonstrations. compression result D = {𝑥𝑖} 𝑖=1,e𝐿D
 (ii) When a high compression ratio is required, maining budget to the instruction and the question:
token-level dropout as in Li (2023) might make
the compressed prompts too trivial and thus lose Δ𝜏= 𝑘· 𝜏dems𝐿dems −e𝐿D , (3)
vital information from the original prompt. Con- 𝐿ins + 𝐿que
sequently, sentence-level dropout should be em-
 where denote the total number of tokens in D.ployed instead to preserve a certain degree of lin- e𝐿D
guistic integrity. Especially in the case of multiple
 4.2 Iterative Token-level Prompt Compression
redundant demonstrations, we can even perform
 Utilizing perplexity for prompt compression en-demonstration-level control to meet the compres-
 counters the intrinsic limitation, i.e., the indepen-sion requirement.
 dence assumption, similar to the shortcomings of Algorithm 1 illustrates the overall procedure of
 the Mask Language Model (Yang et al., 2019) as:the budget controller.

Derive compression ratio for demonstrations. e𝐿
 ÖWe first compute the compression rate for demon- 𝑝(ex) = 𝑝(e𝑥𝑖|e𝑥<𝑖)
strations 𝜏dems according to the target overall com- 𝑖=1 (4)
pression rate 𝜏and the pre-defined compression 𝐿′
 ≈𝑝(x′) = Ö 𝑥<𝑖),rate for instructions and questions, i.e., 𝜏ins and 𝑝(𝑥𝑖|e𝑥<𝑖,
𝜏que, respectively. 𝑖=1
 where x′ = (xins, xD, xque) is the original prompt 𝜏𝐿−(𝜏ins𝐿ins + 𝜏que𝐿que)
 𝜏dems = . (2) after demonstration-level compression; xD is the
 𝐿dems
 concatenation of all demonstrations in D; e𝑥is the
Demonstration-level prompt compression. final compressed prompt; e𝑥<𝑖and 𝑥<𝑖denote the
With the derived 𝜏dems for demonstrations, we preserved and compressed tokens before the 𝑖-th
then perform a coarse-grained demonstration-level token 𝑥𝑖; 𝐿′ and e𝐿denote the numbers of all tokens
prompt compression: we construct D, a subset of in x′ and ex, respectively.
demonstrations from xdems. Here we propose an iterative token-level prompt
 Specifically, we first employ a small language compression (ITPC) algorithm to mitigate the inacmodel M𝑠, such as GPT-2 or LLaMA, to com- curacy introduced by the conditional independence
pute the perplexity of each demonstration in xdems. assumption. Algorithm 2 shows the pseudo codes.
Then, we select demonstrations in descending order Specifically, we first divide the target prompt x′
of their perplexity values, until adding one more into several segments S = {s1, s2, ..., s𝑚}. And

<!-- Page 5 -->
then, we use the smaller model M𝑠to obtain the 5 Experiments
perplexity distribution of all segments. The com-
 5.1 Settingspressed prompt obtained from each segment is
concatenated to the subsequent segment, enabling Datasets To comprehensively assess the effecmore accurate estimation of the conditional prob- tiveness of compressed prompts in retaining LLM
ability. The corresponding probability estimation abilities, we evaluated their performance across
function can be formulated as: four datasets. For reasoning and in-context learn-
 𝑗 ing (ICL), we use GSM8K (Cobbe et al., 2021)
 Í 𝑘e𝐿𝑠,𝑘 and BBH (Suzgun et al., 2022). As for contextual
 Ö 𝑗) = 𝑗) understanding, we use ShareGPT (sha, 2023) for 𝑝(es 𝑝(e𝑠𝑗,𝑖|e𝑠𝑗,<𝑖, es< 𝑖=1
 (5) conversation and Arxiv-March23 (Li, 2023) for
 𝐿𝑠, 𝑗+Í𝑗−1𝑘 e𝐿𝑠,𝑘 summarization. It’s worth noting that neither the
 Ö small LM nor the target LLMs used in this paper ≈ 𝑝(𝑠𝑗,𝑖|𝑠𝑗,<𝑖, es< 𝑗),
 𝑖=1 have seen any of the evaluation datasets, especially
 the last two which were newly collected this year.
where 𝑠𝑗,𝑖denotes the 𝑖-th token in the 𝑗-th seg- We followed the experimental setup of previous
ment, 𝐿𝑠, 𝑗and e𝐿𝑠, 𝑗represent the token length of 𝑗- work (Fu et al., 2023a; Li, 2023) for the usage of
th original and compressed segment, respectively. these datasets. Please refer to Appendix A.1 for
 When the conditional probabilities for each seg- detailed information.
ment 𝑝(s 𝑗) are obtained, the compression ratio
threshold 𝛾𝑗w.r.t. s 𝑗are dynamically calculated Evaluation Following Cobbe et al. (2021), Fu
based on the PPL distribution and the correspond- et al. (2023a), and Li (2023), we utilize the Exing compression ratio 𝜏s𝑗, where act Match as the evaluation metric for GSM8K
 and BBH. We use BLEU (Papineni et al., 2002),
 𝜏ins + Δ𝜏, if s 𝑗from xins, ROUGE (Lin, 2004), and BERTScore (Zhang et al.,
  𝜏s𝑗= 𝜏dems, if s 𝑗from xD, (6) 2020) as the evaluation metrics for ShareGPT and
 Arxiv-March23.
 if s 𝑗from xque.  𝜏que + Δ𝜏,
 Implementation Details In this paper, we em-
 Finally, tokens in each s 𝑗with the PPL greater ploy the GPT-3.5-Turbo-0301 and the Claude-v1.3
than 𝛾𝑗are retained in the compressed prompt. as the target LLMs, which can be accessed via Ope-
 nAI2 and Claude API3. To improve the stability
 of outputs produced by LLMs we apply greedy es𝑗= {𝑠𝑗,𝑖|𝑝(𝑠𝑗,𝑖) > 𝛾𝑗} (7)
 decoding with a temperature of 0 across all exper4.3 Distribution Alignment iments. The Alpaca dataset (Taori et al., 2023) is
 exclusively employed for aligning small language
To narrow the gap between the distribution of the
 models with black-box LLMs, and is not utilized
LLM and that of the small language model used
 in the evaluation process. In our experiments, we
for prompt compression, here we align the two
 utilize either Alpaca-7B4 or GPT2-Alpaca as the
distributions via instruction tuning.
 small pre-trained language model M𝑠for compres-
 Specifically, we start from a pre-trained small
 sion. We implement our approach based on Pylanguage model M𝑠and use the data generated by Torch 1.12.05 and Huggingface’s Transformers6.
the LLM to perform instruction tuning on M𝑠. The We set the granular control coefficient 𝑘to 2. We
optimization of M𝑠can be formulated as: use the pre-defined compression rates 𝜏ins = 0.85
 𝑁 and 𝜏que = 0.9 for instructions and questions. The " # 1 ∑︁ min θ𝑠E 𝑁 L x𝑖, y𝑖,LLM; θM𝑠 , (8) segment size used in the iterative token-level com- 𝑖=1 pression is set to 100.

 2https://platform.openai.com/where 𝜃M𝑠 denotes the parameters of M𝑠,
 3https://anthropic.com/
(x𝑖, yLLM𝑖 ) denotes the pair of instruction x𝑖and 4https://github.com/tatsu-lab/stanford_alpaca
the LLM generated texts yLLM𝑖 , 𝑁is the number of 5https://pytorch.org/
all examples used for instruction tuning. 6https://github.com/huggingface/transformers

<!-- Page 6 -->
 ShareGPT Arxiv-March23
 Methods
 BLEU Rouge1 Rouge2 RougeL BS F1 Tokens 1/𝜏BLEU Rouge1 Rouge2 RougeL BS F1 Tokens 1/𝜏

 Constraint I 2x constraint 350 tokens constraint
 Sentence Selection 28.59 46.11 31.07 37.94 88.64 388 1.5x 22.77 50.1 25.93 33.63 88.21 379 4x
 Selective-Context 25.42 46.47 29.09 36.99 88.92 307 1.9x 21.41 51.3 27.94 36.73 89.60 356 4x
 Ours 27.36 48.87 30.32 38.55 89.52 304 1.9x 23.15 54.21 32.66 42.74 90.33 345 4x
 Constraint II 3x constraint 175 tokens constraint
 Sentence Selection 18.94 35.17 18.96 26.75 85.63 255 2.3x 12.41 38.91 14.25 26.72 87.09 229 7x
 Selective-Context 15.79 38.42 20.55 28.89 87.12 180 3.3x 12.23 42.47 19.48 29.47 88.16 185 8x
 Ours 19.55 40.81 22.68 30.98 87.70 177 3.3x 13.45 44.36 24.86 34.94 89.03 176 9x

Table 1: Performance of different methods under different target compression ratios on the conversation (ShareGPT)
and summarization (Arxiv-March23) task.

 GSM8K BBH
 Methods
 EM Tokens 1/𝜏 EM Tokens 1/𝜏 BBH, ShareGPT, and Arxiv-March23. It can be
 Full-shot 78.85 2,366 - 70.07 774 - seen that our proposed method consistently out-
 1-shot constraint performs the prior methods by a large margin in
 1-shot 77.10 422 6x 69.60 284 3x almost all experiments.
 Selective-Context 53.98 452 5x 54.27 276 3x
 Specifically, on GSM8K and BBH, the reason- GPT4 Generation 71.87 496 5x 27.13 260 3x
 Ours 79.08 446 5x 70.11 288 3x ing and in-context learning-related benchmark, our
 half-shot constraint method even achieves slightly higher results than
 Sentence Selection 72.33 230 10x 39.56 175 4x the full-shot approach, while also delivering im-
 Selective-Context 52.99 218 11x 54.02 155 5x
 pressive compression ratios (1/𝜏) of 5x and 3x GPT4 Generation 68.61 223 11x 27.09 161 5x
 Ours 77.41 171 14x 61.60 171 5x respectively, with the 1-shot constraint. This well
 quarter-shot constraint demonstrates that our compressed prompts effec-
 Sentence Selection 66.67 195 12x 46.00 109 7x tively retain the reasoning information contained
 Selective-Context 44.20 157 15x 47.37 108 7x
 in the original prompt. As the compression ratio GPT4 Generation 56.33 188 20x 26.81 101 8x
 Ours 77.33 117 20x 56.85 110 7x increases, i.e., under the half-shot and quarter-shot
 zero-shot 48.75† 11 215x 32.32 16 48x constraints, the performance experiences a slight
 Simple Prompt 74.9 691 3x - - - decline. For instance, on GSM8K, the EM scores
 will decrease by 1.44 and 1.52, respectively, deTable 2: Performance of different methods under differ-
 spite compression ratios as high as 14x and 20x.
ent target compression ratios on the GSM8K mathemati-
 On BBH, our approach achieves compression ra-cal reasoning and Big-bench Hard (BBH) datasets. †We
also include the instruction of the prompt in zero-shot tios of 5x and 7x with the EM score decreasing
experiments for a vertical comparison. by 8.5 and 13.2 points, respectively. In fact, this
 performance is already quite satisfactory, as it ap-
 proaches the score of 62.0 achieved by PaLM-540B
Baselines We consider the following baselines:
 in half-shot constraint. Our case study reveals
 • GPT4-Generation: Instruct GPT-4 to com- that this declined performance on BBH is mainly
 press the original prompt. We used ten sets due to challenging reasoning tasks, such as track-
 of instructions here and reported the best re- ing_shuffled_objects_seven_objects.
 sults. Appendix C displays the instructions Moreover, on ShareGPT and Arxiv-March23,
 we employed. two contextual understanding benchmarks, we can
 • Random Selection: Random select the demon- see that our approach achieves acceleration ratios
 strations or sentences of the original prompt. of 9x and 3.3x with a high BERTScore F1, indi-
 cating that our approach successfully retains the
 • Selective-Context (Li, 2023): Use the phrase-
 semantic information of the initial prompts.
 level self-information from a small language
 model to filter out less informative content. 5.3 Analysis on Reasoning & ICL Tasks.
 We use the same small LM, i.e., Alpaca-7B
 Here we analyze the performance of our approach for a fair comparison.
 and baseline methods on the difficult reasoning and
5.2 Main Results in-context learning (ICL) benchmarks GSM8K and
Table 1 and 2 report the results of our approach BBH.
alongside those baseline methods on GSM8K, We notice that our approach shows significant

<!-- Page 7 -->
performance improvements over the strong base- EM Tokens 1/𝜏
line Selective-Context under all settings. We Ours 79.08 439 5x
conjecture that, as relying on phrase-level self- - w/o Iterative Token-level Prompt Compression 72.93 453 5x
 - w/o Budget Controller 73.62 486 5x
information, Selective-Context is prone to lose - w/o Dynamic Compression Ratio 77.26 457 5x
critical reasoning information during the chain-of- - w/ Random Selection in Budget Controller 72.78 477 5x
 - w/o Distribution Alignment 78.62 452 5x
thought process. Especially on GSM8K, its perfor- - w/ Remove Stop Words 76.27 1,882 1.3x
mance is lower than ours by 33.10 points at a compression ratio of 20x. The inferior performance of Table 3: Ablation study on GSM8K in 1-shot constraint.
Sentence Selection suggests that it may face similar
issues of fragmentary reasoning logic. Surprisingly,
 ered. We conjecture this variant may lose essen-though GPT-4 has demonstrated its strong text gen-
 tial information in the prompt, especially for low-eration capability, the suboptimal performance on
 frequency keywords that frequently appear in theprompt compression indicates that the generated
 given prompt. When comparing Ours with w/oprompts may omit crucial details from the original
 Dynamic Compression Ratio and with w/o Bud-prompt, particularly reasoning steps.
 get Controller, it reveals that different components In addition to the findings mentioned above, the
 of the prompt exhibit varying sensitivity. Instruc-experiments also demonstrate that our method can
 tions and questions necessitate a lower compres-preserve the ICL capacity of prompts for LLMs.
 sion ratio. To balance the relationship betweenCompared to the zero-shot results, our approach
 compression ratio and language integrity, introduc-exhibits significant performance improvements of
 ing a demonstration or sentence-level filter better51.55 and 24.53 even with the largest compression
 preserves sufficient linguistic information, even atratios. Notably, on GSM8K, our 20x compressed
 higher compression ratios. Ours w/ Random Selec-prompt outperforms the 8-shot 3-step CoT by 2.43,
 tion in Budget Controller indicates that selectingfurther suggesting that our method can effectively
 sentences or demonstrations based on perplexityretain the reasoning information.
 can better identify information-rich sentences for
5.4 Ablation target LLMs. Distribution Alignment allows small
 LMs to generate distributions that more closely reTo validate the contributions of different compo-
 semble those of target LLMs, resulting in a further
nents in our approach, we introduce five variants
 improvement of 0.56 on GSM8K.
of our model for ablation study: i) Ours w/o Iterative Token-level Compression, which performs 5.5 Discussion
token-level compression in a single inference rather
than iteratively. ii) Ours w/o Budget Controller, Different Target LLMs Here we test our method
which directly employs ITPC with the same com- with Claude-v1.3 as the target LLM to demonpression ratio for all components. iii) Ours w/o strate its generalizability across different black-box
Dynamic Compression Ratio, which uses the same LLMs in addition to the GPT series models. Due
compression ratio for all components. iv) Ours to the limitation of API cost, we only consider the
w/ Random Selection in Budget Controller, which scenarios with one-shot constraint and half-shot
randomly selects demonstrations or sentences for constraint. Similarly, we employe Alpaca-7B as
demonstration-level prompt compression. v) Ours the small language model for the challenges in colw/o Distribution Alignment, which removes the lecting alignment data. As shown in Table 4, our
distribution alignment module of our approach and method can achieve improvements over the simple
directly use the pre-trained LLaMA-7B as the small prompt by 0.8 and 1.7 EM points with compression
language model. vi) Ours w/ Remove Stop Words, ratios of 5x and 14x, respectively.
which removes the stop words in original prompts
using NLTK7. Table 3 shows the results. EM Tokens 1/𝜏
 Comparing Ours with w/o Iterative Token-level Ours in 1-shot constraint 83.51 439 5x
Prompt Compression, we observe a significant de- Ours in half-shot constraint 82.61 171 14x
 Simple Prompt 81.8 691 3x
cline in Exact Match when the conditional dependence between compressed tokens is not consid- Table 4: Ours method on GSM8K using Claude-v1.3.

 7https://www.nltk.org/

<!-- Page 8 -->
Different Small LMs We further test our ap- 400 GSM8Kproach with different small language models: we
 BBHfine-tune the GPT2-small on the Alpaca dataset and Length
 300 ShareGPT
use it as the small LM for our system. As shown in Token ArxivTable 5, the results obtained by Alpaca finetuned
GPT2-small are weaker than those obtained by 200
Alpaca-7B with a performance drop of 2.06, 0.99,and 1.06 EM points at different compression ratios. Generation 100
This is due to the significant distribution discrep-
 5 10 15 20
ancy between the small LM and the target LLM. Compression Ratio
Even with distribution alignment, it is still difficult to directly estimate the target LLM using the Figure 2: The distribution of generated token lengths at
distribution from the small language model. Sim- varying compression ratios (1/𝜏).
ilar observations have been reported in Li (2023).
However, benefiting from the proposed budget con-
 Lingua: the number of tokens involved in computatroller and the iterative token-level prompt com-
 tion and the end-to-end latency.
pression algorithm, our approach achieves satisfac-
 The overall computation of our system is the
tory results in difficult tasks such as reasoning even
 sum of the prompt compression and the following
with the less powerful GPT2-Small as the small
 inference. This can be formulated as:
language model.
 𝑐= (𝐿+ 𝑘𝐿/𝜏+ 𝐿/𝜏) · 𝑐small + 𝐿/𝜏· 𝑐LLMs, (9)
 EM Tokens 1/𝜏
 where 𝑐small and 𝑐LLMs represent the per token com-
 Ours with GPT2 in 1-shot constraint 77.02 447 5x putation load of the small LM and LLM, respec-
 Ours with GPT2 in half-shot constraint 76.42 173 14x
 Ours with GPT2 in quarter-shot constraint 76.27 128 18x tively. 𝐿, 𝑘𝐿/𝜏, and 𝐿/𝜏are the numbers of to-
 ken inferences for the budget controller, the perTable 5: Our method on GSM8K with GPT2-Alpaca as plexity calculation of tokens to compress in ITPC,
the small language model. and the conditioned perplexity calculation of com-
 pressed results in ITPC (using KV cache), respec-
 tively. Assuming that the small LM has the same
The Generation Results of Compressed Prompt system optimizations as the LLMs, such as the
Appendix E displays several compressed prompts use of FasterTransformer8 and quantization techalong with following generation texts. It is evident niques, we can estimate the ratio between 𝑐small
that the compressed prompts can still guide the gen- and 𝑐LLMs based on model parameters: 𝑐small ≈
eration of multi-step reasoning outcomes similar to 7/175𝑐LLMs = 1/25𝑐LLMs. When 𝜏= 5, we have
the original ones. In contrast, prompts compressed 𝑐≈0.264 · 𝐿𝑐LLMs ≈1/4 · 𝐿𝑐LLMs. That is, we
using Selective-Context exhibit errors in reason- can achieve nearly 4x savings in computational reing logic. This highlights the effectiveness of our sources when using the smaller LM with a prompt
method in preserving crucial semantic information compression rate of 5x.
while retaining reasoning capabilities.
 As depicted in Figure 2, we also analyze the re- 1/𝜏 1x 2x 5x 10x
lationship between the compression ratio and the End-to-End w/o LLMLingua 8.6 - - -
length of the corresponding generated texts. It can End-to-End w/ LLMLingua - 4.9(1.7x) 2.3(3.3x) 1.3(5.7x)
be observed that as the compression ratio increases, LLMLingua - 0.8 0.3 0.2
the text length produced by target LLMs tends to
 Table 6: Latency (s) comparison on GSM8K.
decrease, albeit with varying degrees across different datasets. This indicates that prompt compres- Table 6 shows the end-to-end latency of different
sion not only saves computational resources in the systems on a V100-32G GPU with a compression
input but also contributes to computational savings rate from 1x to 10x. We can see that LLMLingua
in the generation stage. has a relatively small computation overhead and
 can achieve a speedup ranging from 1.7x to 5.7x.Overhead of LLMLingua We explore two key
factors to study the computation overhead of LLM- 8https://github.com/NVIDIA/FasterTransformer

<!-- Page 9 -->
Recovering the Compressed Prompt using demonstrates the compatibility and adaptability of
LLMs Appendix D shows some examples re- our approach in various LLMs settings.
stored from the compressed prompts by using GPT49. It is evident that LLMs can effectively compre- 6 Conclusion
hend the semantic information in the compressed
 We introduce a coarse-to-fine algorithm for prompt
prompts, even if it might be challenging for humans.
 compression, named LLMLingua, which is based
Additionally, we notice that how much information
 on the small LM’s PPL for black-box LLMs. Our
GPT-4 can recover depends on the compression
 approach consists of three modules: Budget Conratio and the small language model we use. For
 troller, Iterative Token-level Compression, and
instance, in Figure 4, the prompt compressed using
 Alignment. We validate the effectiveness of our
Alpaca-7B is restored to its complete 9-step rea-
 approach on 4 datasets from different domains, i.e.,
soning process, while in Figure 5, the prompt com-
 GSM8K, BBH, ShareGPT, and Arxiv-March23,
pressed with GPT2-Alpaca can only be restored to
 demonstrating that our method achieves state-ofa 7-step reasoning process, with some calculation
 the-art performance across all datasets, with up
errors.
 to 20x compression with only a 1.5 point perforCompare with Generation-based Methods We mance drop. Moreover, we observe that LLMs
do not develop our approach based on LLM gen- can effectively restore compressed prompts, and
eration primarily for three reasons: i) The content prompt compression contributes to a reduction in
and length of the generated text are uncontrollable. generated text length. Our approach holds substanUncontrollable length requires more iterations to tial practical implications, as it not only reduces
satisfy the constraint of the compression ratio. Un- computational costs but also offers a potential solucontrollable content leads to low overlap between tion for accommodating longer contexts in LLMs.
the generated text and the original prompt, par- The method of compressing prompts has the poticularly for complex prompts with multi-step in- tential to enhance downstream task performance
ference, which may lose significant amounts of by compressing longer prompts and to improve the
reasoning paths or even generate completely unre- LLMs’s inference efficiency by compressing the
lated demonstrations. ii) The computational cost KV cache.
is high. Small language models struggle to handle
 Limitationssuch complex tasks, and using models like GPT-4
for compression would further increase computa- There are also some limitations in our approach.
tional overhead. Moreover, even powerful genera- For instance, we might observe a notable pertion models like GPT-4 struggle to retain effective formance drop when trying to achieve excesinformation from prompts as shown in Table 2. sively high compression ratios such as 25x-30x
iii) The compressed prompts obtained from genera- on GSM8K, as shown in Figure 3.
tion models are complete and continuous sentences,
usually resulting in a lower compression ratio com-
 78.85pared to our coarse-to-fine method.

Compare with Prompt Engineering methods 70
Our method is orthogonal to Prompt Engineering Match Ours
methods, such as prompt retrieval and prompt or- 60 Random Selection
dering. Our work focuses on compressing well- Exact GPT4-Generation
designed prompts, and it performs well on com-
 50 Selective-Context
plex and fine-tuned prompts like GSM8K. More- Ours w/ GPT2
over, the perplexity-based demonstration filtering
method used in our budget controller can also be 5 10 15 20 25 30
 Compression Ratioapplied to scenarios such as prompt retrieval. This

 9An intriguing observation is that GPT-3.5-Turbo struggles Figure 3: The performance of various prompt comto reconstruct compressed prompts, while GPT-4 has demon- pression methods at different compression ratios (1/𝜏)
strated an ability to do so. This contrast in performance could on GSM8K. The dashed line corresponds to the Exact
suggest that recovering compressed prompts is an emergent
 Match score obtained from the full-shot prompt.ability that arises in more advanced language models.

<!-- Page 10 -->
 It is shown that as the compression ratio in- Tim Dettmers, Mike Lewis, Younes Belkada, and Luke
creases especially around 25x-30x, all methods as Zettlemoyer. 2022. GPT3.int8(): 8-bit matrix mul-
 tiplication for transformers at scale. In Advances inwell as ours will experience a substantial perfor-
 Neural Information Processing Systems.
mance drop. In comparison with other methods,
this performance drop derived from our approach Elias Frantar and Dan Alistarh. 2023. SparseGPT: Mas-
 sive language models can be accurately pruned inis significantly shifted to much higher compression
 one-shot. In International Conference on Machine
ratios. We owe this to the Budget Controller and Learning.
the Iterative Token-level Prompt Compression algorithm, which enable our method to maintain the Elias Frantar, Saleh Ashkboos, Torsten Hoefler, and Dan
 Alistarh. 2023. OPTQ: Accurate quantization for
original prompt information even at some extreme generative pre-trained transformers. In The Eleventh
compression ratios. The upper limit of the compres- International Conference on Learning Representasion ratio for different prompts varies, depending tions.
on factors such as prompt length, task type, and the
 Yao Fu, Litu Ou, Mingyu Chen, Yuhao Wan, Hao
number of sentences involved. Peng, and Tushar Khot. 2023a. Chain-of-thought
 Additionally, there may be subtle differences hub: A continuous effort to measure large language
between the tokenizers used by the small language models’ reasoning performance. ArXiv preprint,
 abs/2305.17306.model and the black-box LLM, which may result
in an underestimation of the prompt’s token length. Yao Fu, Hao Peng, Ashish Sabharwal, Peter Clark, and
 Tushar Khot. 2023b. Complexity-based prompting
 for multi-step reasoning. In The Eleventh InternaReferences tional Conference on Learning Representations.

2023. Sharegpt. https://sharegpt.com/. Tao Ge, Jing Hu, Li Dong, Shaoguang Mao, Yan Xia,
 Xun Wang, Si-Qing Chen, and Furu Wei. 2022.
Udit Arora, William Huang, and He He. 2021. Types Extensible prompts for language models. ArXiv
 of out-of-distribution texts and how to detect them. preprint, abs/2212.00616.
 In Proceedings of the 2021 Conference on Empiri-
 cal Methods in Natural Language Processing, pages Tao Ge, Jing Hu, Xun Wang, Si-Qing Chen, and Furu
 10687–10701, Online and Punta Cana, Dominican Wei. 2023. In-context autoencoder for context com-
 Republic. Association for Computational Linguistics. pression in a large language model. ArXiv preprint,
 abs/2307.06945.
Daniel Bolya, Cheng-Yang Fu, Xiaoliang Dai, Peizhao
 Zhang, Christoph Feichtenhofer, and Judy Hoffman. Henry Gilbert, Michael Sandborn, Douglas C Schmidt,
 2023. Token merging: Your vit but faster. In The Jesse Spencer-Smith, and Jules White. 2023. Seman-
 Eleventh International Conference on Learning Rep- tic compression with large language models. ArXiv
 resentations. preprint, abs/2304.12512.

Harrison Chase. 2022. LangChain. Saurabh Goyal, Anamitra Roy Choudhury, Saurabh
 Raje, Venkatesan T. Chakaravarthy, Yogish Sabhar-Alexis Chevalier, Alexander Wettig, Anirudh Ajith, and
 wal, and Ashish Verma. 2020. Power-bert: Accel- Danqi Chen. 2023. Adapting language models to
 erating BERT inference via progressive word-vector compress contexts. ArXiv preprint, abs/2305.14788.
 elimination. In Proceedings of the 37th International
Wei-Lin Chiang, Zhuohan Li, Zi Lin, Ying Sheng, Conference on Machine Learning, ICML 2020, 13-18
 Zhanghao Wu, Hao Zhang, Lianmin Zheng, Siyuan July 2020, Virtual Event, volume 119 of Proceedings
 Zhuang, Yonghao Zhuang, Joseph E. Gonzalez, Ion of Machine Learning Research, pages 3690–3699.
 Stoica, and Eric P. Xing. 2023. Vicuna: An open- PMLR.
 source chatbot impressing gpt-4 with 90%* chatgpt
 Edward J Hu, yelong shen, Phillip Wallis, Zeyuan Allen- quality.
 Zhu, Yuanzhi Li, Shean Wang, Lu Wang, and Weizhu
Karl Cobbe, Vineet Kosaraju, Mohammad Bavarian, Chen. 2022. LoRA: Low-rank adaptation of large
 Mark Chen, Heewoo Jun, Lukasz Kaiser, Matthias language models. In International Conference on
 Plappert, Jerry Tworek, Jacob Hilton, Reiichiro Learning Representations.
 Nakano, et al. 2021. Training verifiers to solve math
 word problems. ArXiv preprint, abs/2110.14168. Gyuwan Kim and Kyunghyun Cho. 2021. Length-
 adaptive transformer: Train once with length drop,
Grégoire Delétang, Anian Ruoss, Paul-Ambroise use anytime with search. In Proceedings of the 59th
 Duquenne, Elliot Catt, Tim Genewein, Christo- Annual Meeting of the Association for Computational
 pher Mattern, Jordi Grau-Moya, Li Kevin Wenliang, Linguistics and the 11th International Joint Confer-
 Matthew Aitchison, Laurent Orseau, et al. 2023. Lan- ence on Natural Language Processing (Volume 1:
 guage modeling is compression. ArXiv preprint, Long Papers), pages 6501–6511, Online. Association
 abs/2309.10668. for Computational Linguistics.

<!-- Page 11 -->
Sehoon Kim, Sheng Shen, David Thorsley, Amir Gho- Mirac Suzgun, Nathan Scales, Nathanael Schärli, Se-
 lami, Woosuk Kwon, Joseph Hassoun, and Kurt bastian Gehrmann, Yi Tay, Hyung Won Chung,
 Keutzer. 2022. Learned token pruning for transform- Aakanksha Chowdhery, Quoc V Le, Ed H Chi, Denny
 ers. In Proceedings of the 28th ACM SIGKDD Con- Zhou, , and Jason Wei. 2022. Challenging big-bench
 ference on Knowledge Discovery and Data Mining, tasks and whether chain-of-thought can solve them.
 pages 784–794. ArXiv preprint, abs/2210.09261.

Yucheng Li. 2023. Unlocking context constraints of Rohan Taori, Ishaan Gulrajani, Tianyi Zhang, Yann
 llms: Enhancing context efficiency of llms with self- Dubois, Xuechen Li, Carlos Guestrin, Percy Liang,
 information-based content filtering. ArXiv preprint, and Tatsunori B. Hashimoto. 2023. Stanford alpaca:
 abs/2304.12102. An instruction-following llama model. https://
 github.com/tatsu-lab/stanford_alpaca.
Chin-Yew Lin. 2004. ROUGE: A package for auto-
 matic evaluation of summaries. In Text Summariza- Jason Wei, Xuezhi Wang, Dale Schuurmans, Maarten
 tion Branches Out, pages 74–81, Barcelona, Spain. Bosma, brian ichter, Fei Xia, Ed H. Chi, Quoc V Le,
 Association for Computational Linguistics. and Denny Zhou. 2022. Chain of thought prompt-
 ing elicits reasoning in large language models. In
Ilya Loshchilov and Frank Hutter. 2019. Decoupled
 Advances in Neural Information Processing Systems.
 weight decay regularization. In 7th International
 Conference on Learning Representations, ICLR 2019, David Wingate, Mohammad Shoeybi, and Taylor
 New Orleans, LA, USA, May 6-9, 2019. OpenRe- Sorensen. 2022. Prompt compression and contrastive
 view.net. conditioning for controllability and toxicity reduction
 in language models. In Findings of the AssociationKimberly T Mai, Toby Davies, and Lewis D Griffin.
 for Computational Linguistics: EMNLP 2022, pages 2022. Self-supervised losses for one-class textual
 5621–5634, Abu Dhabi, United Arab Emirates. As- anomaly detection. ArXiv preprint, abs/2204.05695.
 sociation for Computational Linguistics.
Ali Modarressi, Hosein Mohebbi, and Moham-
 mad Taher Pilehvar. 2022. AdapLeR: Speeding up Qianhui Wu, Huqiang Jiang, Haonan Yin, Börje F. Karls-
 inference by adaptive length reduction. In Proceed- son, and Chin-Yew Lin. 2023. Multi-level knowledge
 ings of the 60th Annual Meeting of the Association distillation for out-of-distribution detection in text.
 for Computational Linguistics (Volume 1: Long Pa- In Proceedings of the 61th Annual Meeting of the
 pers), pages 1–15, Dublin, Ireland. Association for Association for Computational Linguistics (Long Pa-
 Computational Linguistics. pers).

Jesse Mu, Xiang Lisa Li, and Noah Goodman. 2023. Guangxuan Xiao, Ji Lin, Mickael Seznec, Julien De-
 Learning to compress prompts with gist tokens. mouth, and Song Han. 2023. Smoothquant: Accu-
 ArXiv preprint, abs/2304.08467. rate and efficient post-training quantization for large
 language models. In International Conference on
Kishore Papineni, Salim Roukos, Todd Ward, and Wei- Machine Learning.
 Jing Zhu. 2002. Bleu: a method for automatic evalu-
 ation of machine translation. In Proceedings of the Can Xu, Qingfeng Sun, Kai Zheng, Xiubo Geng,
 40th Annual Meeting of the Association for Compu- Pu Zhao, Jiazhan Feng, Chongyang Tao, and Daxin
 tational Linguistics, pages 311–318, Philadelphia, Jiang. 2023. Wizardlm: Empowering large lan-
 Pennsylvania, USA. Association for Computational guage models to follow complex instructions. ArXiv
 Linguistics. preprint, abs/2304.12244.

Richard Clark Pasco. 1976. Source coding algorithms Nan Yang, Tao Ge, Liang Wang, Binxing Jiao, Daxin
 for fast data compression. Ph.D. thesis, Citeseer. Jiang, Linjun Yang, Rangan Majumder, and Furu
 Wei. 2023. Inference with reference: Lossless acYongming Rao, Wenliang Zhao, Benlin Liu, Jiwen Lu,
 celeration of large language models. ArXiv preprint,
 Jie Zhou, and Cho-Jui Hsieh. 2021. Dynamicvit: Ef-
 abs/2304.04487.
 ficient vision transformers with dynamic token spar-
 sification. In Advances in Neural Information Pro- Zhilin Yang, Zihang Dai, Yiming Yang, Jaime G. Car-
 cessing Systems. bonell, Ruslan Salakhutdinov, and Quoc V. Le. 2019.
 Xlnet: Generalized autoregressive pretraining forJorma J Rissanen. 1976. Generalized kraft inequality
 language understanding. In Advances in Neural In- and arithmetic coding. IBM Journal of research and
 formation Processing Systems 32: Annual Confer- development, 20(3):198–203.
 ence on Neural Information Processing Systems 2019,
Claude E Shannon. 1951. Prediction and entropy NeurIPS 2019, December 8-14, 2019, Vancouver, BC,
 of printed english. Bell system technical journal, Canada, pages 5754–5764.
 30(1):50–64.
 Lei Zhang, Yuge Zhang, Kan Ren, Dongsheng Li, and
Ilya Sutskever. 2023. A theory of unsupervised Yuqing Yang. 2023. Mlcopilot: Unleashing the
 learning. https://simons.berkeley.edu/talks/ power of large language models in solving machine
 ilya-sutskever-openai-2023-08-14. learning tasks. ArXiv preprint, abs/2304.14979.

<!-- Page 12 -->
Tianyi Zhang, Varsha Kishore, Felix Wu, Kilian Q. A.2 Other Implementation Details
 Weinberger, and Yoav Artzi. 2020. Bertscore: Evalu-
 ating text generation with BERT. In 8th International All experiments were conducted using a Tesla
 Conference on Learning Representations, ICLR 2020, V100 (32GB). We trained the GPT2-Alpaca model
 Addis Ababa, Ethiopia, April 26-30, 2020. OpenRe- on the Alpaca dataset12 for eight epochs using
 view.net. a learning rate of 1e-4 and the AdamW opti-
 mizer (Loshchilov and Hutter, 2019). The training
Wangchunshu Zhou, Yuchen Eleanor Jiang, Ryan Cot-
 process took approximately 150 minutes to com- terell, and Mrinmaya Sachan. 2023. Efficient prompt-
 ing via dynamic in-context learning. ArXiv preprint, plete. We use tiktoken13 and GPT-3.5-Turbo model
 abs/2305.11170. to count all the tokens.

A Experiment Details B Economic Cost

A.1 Dataset Details
 GSM8K BBH ShareGPT Arxiv
GSM8K A widely used math reasoning dataset Original 5.2 12.8 0.7 1.3
comprising 8,000 problems, including a 1,300 prob- Ours 0.5 4.8 0.3 0.2
lems test set that assesses models’ capabilities in
arithmetic reasoning and formulating mathematical Table 7: The inference costs($) for various datasets
 using GPT-3.5-Turbo.steps using language (Cobbe et al., 2021). For this
dataset, we employ the complex multi-step CoT
prompt (Fu et al., 2023b)10 as the original prompt. Table 7 displays the estimated inference costs
 for various datasets, according to the pricing of
BBH A suite of language and symbolic reason- GPT-3.5-Turbo. Our approach showcases signifiing tasks, consisting of 6,500 problems across 23 cant savings in computational resources and monsubsets, specifically designed to evaluate chain-of- etary expenditures, with cost reductions of $4.7,
thought prompting. In our experiment, we adopt $8.0, $0.4, and $0.8 observed in the GSM8K, BBH,
the 3-shot CoT prompt11 as the original prompts, ShareGPT, and Arxiv datasets, respectively.
following the approach described by Suzgun et al.
(2022). C Instructions used in GPT-4 Generation

ShareGPT A conversation dataset from The instructions we used in the GPT-4 Generation
ShareGPT.com platform (sha, 2023) which in- are shown below:
cludes users sharing conversations with ChatGPT
 1. Could you please rephrase the paragraph toin different languages and in various scenarios
 make it short, and keep 5% tokens?(e.g., coding, chitchat, writing assistant, etc.). We
use a dataset of 575 samples provided by Li (2023) 2. Condense the passage to retain only 5% of its
as our test set. We use all dialogues except the original tokens, while preserving its meaning.
final round as the prompt and generate results with 3. Short the sentences to 200 tokens.
GPT-3.5-Turbo as the reference. 4. Trim the text down to 200 tokens in total.

 5. Please provide a concise summary of the givenArxiv-March23 A dataset consisting of latest
 examples in several sentences, ensuring thatacademic papers created in March 2023 from the
 all reasoning information is included.arXiv preprint repository. We use 500 data items
collected by Li (2023) as the test set. Due to the 6. Summarize the provided examples in a few
excessive length of some articles, we take the first sentences, maintaining all essential reasoning
five sections of each article and truncate each sec- aspects.
tion to 10,000 characters. Then, we concatenate 7. Remove redundancy and express the text conthese sections to form the original prompt and use cisely in English, ensuring that all key inGPT-3.5-Turbo to generate the summary as the ref- formation and reasoning processes are preerence. served.

 10https://github.com/FranxYao/chain-of-thought-hub 12https://github.com/tatsu-lab/stanford_alpaca
 11https://github.com/suzgunmirac/BIG-Bench-Hard 13https://github.com/openai/tiktoken

<!-- Page 13 -->
 8. Eliminate repetitive elements and present the
 text concisely, ensuring that key details and
 logical processes are retained.
 9. Follow these steps to shorten the given text
 content: 1. First, calculate the amount of
 information contained in each sentence, and
 remove sentences with less information. 2.
 Next, further condense the text by removing
 stop words, unnecessary punctuation, and re-
 dundant expressions. Refine the content while
 ensuring that all key information is retained.
 Let’s do it step by step.
 10. To shorten the given text, follow these steps:
 a) Determine the information value of each
 sentence and remove those with lower value.
 b) Further reduce the text by removing stop
 words, unneeded punctuation, and superflu-
 ous expressions, while making sure to keep
 all vital information intact. Let’s do it step by
 step.

D Recovering Compressed Prompts with
 Large Language Model

In this section, we showcase several examples of
employing black-box LLMs to reconstruct compressed prompts. Specifically, we have selected
three compressed prompts with varying compression ratios, produced by distinct small language
models, on different datasets. These prompts, accompanied by guiding instructions, will serve as
input for the GPT-4 model.

E Cases Study

We present various cases from multiple datasets,
encompassing compressed prompts, outcomes derived from original prompts, outcomes derived
from compressed prompts, and results achieved
utilizing the selective-context approach.

<!-- Page 14 -->
 Original Prompt(9-steps Chain-of-Thought):
 Question: Sam bought a dozen boxes, each with 30 highlighter pens inside, for $10 each box. He
 rearranged five of these boxes into packages of six highlighters each and sold them for $3 per
 package. He sold the rest of the highlighters separately at the rate of three pens for $2. How much
 profit did he make in total, in dollars?

 Let’s think step by step
 Sam bought 12 boxes x $10 = $120 worth of highlighters.
 He bought 12 * 30 = 360 highlighters in total.
 Sam then took 5 boxes × 6 highlighters/box = 30 highlighters.
 He sold these boxes for 5 * $3 = $15
 After selling these 5 boxes there were 360 - 30 = 330 highlighters remaining.
 These form 330 / 3 = 110 groups of three pens.
 He sold each of these groups for $2 each, so made 110 * 2 = $220 from them.
 In total, then, he earned $220 + $15 = $235.
 Since his original cost was $120, he earned $235 - $120 = $115 in profit.
 The answer is 115
 Compressed Prompt:
 : Sam bought a dozen boxes each 30 highl pens inside, $10 each. He reanged five of boxes into of
 six each $3 per. He sold the thelters separately at the of three $2. much make total,
 Lets think step
 bought boxes x0 oflters
 He 2 3ters in
 Sam then boxes 6lters/box 0ters
 He sold these boxes 5
 Afterelling these boxes there 36030lters
 ese00 of three
 sold groups2 each so made *2 $20 from
 In total, he015
 Since his he $ - $120 = $115 in profit.
 The answer is 115
 Recovered Compressed Prompt(9-steps Chain-of-Thought):
 Question: Sam purchased a dozen boxes, each containing 30 highlighter pens, for $10 each. He
 rearranged five of these boxes to have six pens each and sold them for $3 per box. He then sold
 the remaining highlighters individually in groups of three for $2 each. Let’s calculate the total profit.

 Initially, Sam bought 12 boxes with 30 highlighters each, which equals 360 highlighters.
 He spent $10 per box, so his total cost was $120.
 Sam then rearranged 5 boxes to have 6 highlighters per box, which equals 30 highlighters. He sold
 these boxes for $3 each, making $15.
 After selling these boxes, he had 330 highlighters left (360 - 30). He sold these in groups of three,
 which equals 110 groups. He sold each group for $2, making $220 from this.

 In total, Sam made $235 ($15 + $220). Since his initial cost was $120, he earned a
 profit of $115 ($235 - $120).

 The answer is $115.

Figure 4: Recovering the compressed prompt(1/𝜏=17x, Alpaca-7B as small language model) from GSM8K using
GPT-4.

<!-- Page 15 -->
 Compressed Prompt:
 Question: Sam a dozen boxes with 30ighter pens, each Heanged into of3 the separately the of
 three. much total, in
 ’s think step bought boxes $120l
 bought 12 30 = 360
 Sam took boxes 6ighters for $15
 these boxes were 360 - =ighters
 These form 330 = 110 of three these so made * $220 from them.
 In total, then, he earned $220 + $15 = $235.
 Since his original cost was $120, he earned $235 - $120 = $115 in profit.
 The answer is 115
 Recovered Compressed Prompt(7-steps Chain-of-Thought):
 Question: Sam bought a dozen boxes with 30 lighters each. He arranged them into groups of three
 and sold them separately. In total, he spent $120 on the lighters. How much profit did he make?
 Step 1: Sam bought 12 boxes * 30 lighters each = 360 lighters in total.
 Step 2: Sam took 6 boxes of lighters and sold them for $15.
 Step 3: The remaining lighters are 360 - 6 boxes * 30 lighters each = 330 lighters.
 Step 4: These 330 lighters form groups of three, so he made 110 groups * $2 per group = $220
 from them.
 Step 5: In total, he earned $220 + $15 = $235.
 Step 6: Since his original cost was $120, he earned $235 - $120 = $115 in profit.
 The answer is $115.

Figure 5: Recovering the compressed prompt (1/𝜏=19x, GPT2-Alpaca as small language model) from GSM8K
using GPT-4.

 Original Prompt:
 Questions that involve enumerating objects and asking the model to count them.
 Q: I have a blackberry, a clarinet, a nectarine, a plum, a strawberry, a banana, a flute, an orange,
 and a violin. How many fruits do I have?
 A: Let’s think step by step.
 We first identify the fruits on the list and include their quantity in parentheses:
 - blackberry (1)\n - nectarine (1)\n - plum (1)\n - strawberry (1)\n - banana (1)\n - orange (1)
 Now, let’s add the numbers in parentheses: 1 + 1 + 1 + 1 + 1 + 1 = 6. So the answer is 6.
 Compressed Prompt:
 Questions that involve enumerating objects and asking the model to count them.
 : a blackberry, a a ne a a a a, many have
 :’s think
 We first theruits the list and include their in
 - (–
 ’s the numbers in parentheses:1 + 1 = 6. So the answer is 6.
 Recovered Compressed Prompt:
 Question: In a basket, there is a blackberry, an apple, a pear, a plum, a peach, and an orange. How
 many fruits are in the basket?
 Let’s think step by step. We first identify the fruits in the list and include their individual count in
 parentheses: a blackberry (1), an apple (1), a pear (1), a plum (1), a peach (1), and an orange (1).
 Now, let’s add the numbers in parentheses: 1 + 1 + 1 + 1 + 1 + 1 = 6. So the answer is 6. There are
 6 fruits in the basket.

Figure 6: Recovering the compressed prompt(1/𝜏=7x, Alpaca-7B as small language model) from BBH using
GPT-4.

<!-- Page 16 -->
Compressed Prompt:
Follow the given examples and answer the question.
: Sam bought a dozen boxes each 30 highl pens inside, $10 each. He reanged five of boxes into of
six each $3 per. He sold the thelters separately at the of three $2. much make total,
Lets think step
bought boxes x0 oflters
He 2 3ters in
Sam then boxes 6lters/box 0ters
He sold these boxes 5
Afterelling these boxes there 36030lters
ese00 of three
sold groups2 each so made *2 $20 from
In total, he015
Since his he $ - $120 = $115 in profit.
The answer is 115

Q: Toula went to the bakery and bought various types of pastries. She bought 3 dozen
donuts which cost $68 per dozen, 2 dozen mini cupcakes which cost $80 per dozen, and 6 dozen
mini cheesecakes for $55 per dozen. How much was the total cost?
Reference Answer(4-steps):
The total charge for the doughnuts was 3 x $68 = $«3*68=204»204.
The total charge for the mini cupcakes was 2 x $80 = $«2*80=160»160.
The total charge for the mini cheesecakes was 6 x $55 = $«6*55=330»330.
Therefore the total amount Toula paid for the pastries was $204 + $160 + $330 =
$«204+160+330=694» 694 .
The Generation of Compressed Prompt(4-steps):
Toula bought 3 dozen donuts which cost $68 per dozen. So, she spent 3 x 68 = $204 on donuts.
She also bought 2 dozen mini cupcakes which cost $80 per dozen. So, she spent 2 x 80 = $160 on
mini cupcakes.
Finally, she bought 6 dozen mini cheesecakes for $55 per dozen. So, she spent 6 x 55 = $330 on
mini cheesecakes.
Therefore, the total cost was 204 + 160 + 330 = $694.
The answer is 694 .
The Generation of Selective-Context:
A: Toula bought 3 x 12 = «3*12=36»36 donuts.
Then, the total cost of the donuts is 36 x $68 = $«36*68=2448»2448.
How many mini cupcakes did Toula buy in total?
She bought 2 x 12 = «2*12=24»24 mini cupcakes.
So, the total cost of the mini cupcakes is 24 x $80 = $«24*80=1920»1920.
How many mini cheesecakes did Toula buy in total?
She bought 6 x 12 = «6*12=72»72 mini cheesecakes.
Thus, the total cost of the mini cheesecakes is 72 x $55 = $«72*55=3960»3960.
Therefore, the total cost of all the pastries is $2448 + $1920 + $3960 =
$«2448+1920+3960=8328»8328. Answer: 8328 .

 Figure 7: Cases study on GSM8K math reasoning dataset in half-shot constraint.

<!-- Page 17 -->
Compressed Prompt:
Evaluate a random boolean function expressed as a word problem.
Q: Question: Fidel tells the truth.
Jerry saysidel tells truth.
Vina says Jerry
Mill saysina
Raymond saysent
(3)ina says Jerry
() saysina
) saysent
We know from ( that Raymond truth.
So the answer is Yes.
Q: Question: Raymond tells the truth. Sal says Raymond lies. Alexis says Sal lies. Helene says
Alexis lies. Elanor says Helene lies. Does Elanor tell the truth?
A: Let’s think step by step.
The Generation of Full-shot Prompt(5-steps):
(1) Raymond tells the truth. So, we know that Raymond tells the truth.
(2) Sal says Raymond lies. Since we know from (1) that Raymond tells the truth, if Sal says
Raymond lies, then Sal lies.
(3) Alexis says Sal lies. Since we know from (2) that Sal lies, if Alexis says Sal lies, then Alexis
tells the truth.
(4) Helene says Alexis lies. Since we know from (3) that Alexis tells the truth, if Helene says
Alexis lies, then Helene lies.
(5) Elanor says Helene lies. Since we know from (4) that Helene lies, if Elanor says Helene lies,
then Elanor tells the truth.
Now, the question asks: Does Elanor tell the truth? We know from (5) that Elanor tells the truth.
So the answer is 𝑌𝑒𝑠.
The Generation of Compressed Prompt(5-steps):
1. Raymond tells the truth.
2. Sal says Raymond lies. This means Sal lies.
3. Alexis says Sal lies. This means Alexis tells the truth.
4. Helene says Alexis lies. This means Helene lies.
5. Elanor says Helene lies. This means Elanor tells the truth.
So the answer is 𝑌𝑒𝑠.
The Generation of Selective-Context:
1. Raymond says Millicent lies. If Raymond tells the truth, then Millicent lies. If Raymond lies,
then Millicent tells the truth. We don’t know which one is true.
2. Millie says Kristian lies. If Millie tells the truth, then Kristian lies. If Millie lies, then Kristian
tells the truth. We don’t know which one is true.
3. Michaela says Kristian lies. If Michaela tells the truth, then Kristian lies. If Michaela lies, then
Kristian tells the truth. We don’t know which one is true.
4. Osvaldo says Raymond tells the truth. If Osvaldo tells the truth, then Raymond tells the truth. If
Osvaldo lies, then Raymond lies. We don’t know which one is true.
5. Jamey is not mentioned in any of the previous statements, so we don’t know if he tells the truth
or not.
Now let’s move on to the second question.

 Figure 8: Cases study on web_of_lies of BBH reasoning dataset in quarter-shot constraint.

<!-- Page 18 -->
Compressed Prompt:
Please provide the example of the radiation beam spectrum in the TOPAS format, and I will learn
from it.
The beam energy spectrum is composed of 12 energy levels with corresponding weights.
dv:So/SARRPbeam/BeamEnergySpectrumValues: Lists the energy values in the spectrum,
separated by tabs.
From the provided data, we have the following energy values and corresponding weights:
Energy (V)Weight202.15E-0520.12.29E-0520.22.43E0520.32.580520.42.74052.52.9152.63.0
952.7.29052.8.49E-0520.93.71E05213.94E-0521.14.15E05Please let me know if you have any
questions or would like me to assist you further with this data.
Now I’ll give you a table with the spectrum of an ElektaAC and I want you to transform it to the
topas format:
Please provide the table with the spectrum of the Elekta LINAC, and I will transform it into theAS
format for you.
E (MeV) Elekta (6 MV)
0.25 0.173E—04(1.5%)\n .5 .100E03(0.5%)\n .75 .110E03(.5%)\n 1 0.952E—04(0.5%)\n 1.25
.827E04(.5\n .5 .721E04(.\n . .63E04(.\n 2 .55E04(.%)\n . .493E04(0.5%)\n 2.5 .437E04(.6\n
2.5 .386E04(.6\n 3 .34E4(.6\n . .30E4(.6\n . .27E4(.7\n 3.7 0.240E—04(.7\n 4 .21E04(.7\n .
.18E(.8\n . .16E(.8\n .14E(.9\n 5 .121E04(.9\n 5.2 .101E4(1.0\n . .81E0(.1\n . .64E.2\n 6 .45E.4\n
.0.300E—05(1.8\n 6.5 .15E0(.4\n .7 .73E0(.4\n 7 .29E0(.0\n . .88E0(.\n .18E—07(19.8%)\n 7.75
0.277E—08(50.0%)\n 8 0.159E—08(70.8%)
The Generation of Full-shot Prompt:
Thank you for providing the table with the Elekta LINAC spectrum. Here’s the TOPAS format for
the same:
# Spectrum

 s:...Type "Discrete"
 0.25 0.5 0.75 1 1.25 1.5 1.75 2
 2.25 2.5 2.75 3 3.25 3.5 3.75 4
 dv:...Values
 4.25 4.5 4.75 5 5.25 5.5 5.75 6
 6.25 6.5 6.75 7 7.25 7.5 7.75 8
 0.173E-04 0.100E-03 0.110E-03 0.952E-04 0.827E-04 0.721E-04 0.635E-04 0.557E-04
 0.493E-04 0.437E-04 0.386E-04 0.345E-04 0.302E-04 0.270E-04 0.240E-04 0.210E-04
 uv:...Weights
 0.186E-04 0.164E-04 0.142E-04 0.121E-04 0.101E-04 0.817E-05 0.645E-05 0.455E-05
 0.300E-05 0.156E-05 0.734E-06 0.294E-06 0.882E-07 0.188E-07 0.277E-08 0.159E-08
I hope this helps. Let me know if you have any questions or if you need further assistance.
The Generation of Compressed Prompt:
Thank you for providing the table of the EletaAC spectrum. Here’s how it can be formatted in the
TOPAS format:
# Spectrum

 s:...Type "Discrete"
 0.25 1.5 2 3.2 4 5 6 7
 dv:...Values
 7.25 7.5 7.75 8
 173E04 .10E-0 .11E1 .950 .82 .63E-0 .55E0 .4943
 uv:...Weights
 .345E0 .30E0 .27 .24E0 .16E0 .714E52 .64E0 6.45E0
 .30E0 .15E0 .73E0 .294E-06 .882E-07 .188E-07 .277E-08 .159E-08

Please let me know if you have any questions or if you need further assistance with this data.
The Generation of Selective-Context:
I’m sorry, but I cannot see the table you are referring to. Can you please provide me with the table
so I can assist you in transforming it into the TOPAS format?

 Figure 9: Cases study on ShareGPT conversation dataset in 2x constraint.

<!-- Page 19 -->
Compressed Prompt:
Redshift drift in a universe with structure II: Light rays propagated through aian Nbody simulation

koksbang@cp3.sdk
CP3-Orins,mark„ Mmark

Theshiftift is computed alongs propagating through aulated universe on the Newtonian
Nbody simulation code GGET2 with a perturbedmann-Lreer metric in theian gauge.
It is that the meanshiftift is to theift of the meanshift to the precision of the numericalations and
that this is to a high of cancellation between two dominant components theshiftift.
This result is contrary to earlier findings based on inhomogeneous cosmological models exhibiting
cosmic backreaction.
For simplicity, the results neglect contributions from optical drift.
Based on a study of the redshift drift in a Lemaitre-Tolman-Bondi model, the optical drift effects
are estimated to be at most of order 10% of the redshift drift signal.
The Generation of Full-shot Prompt:
The article discusses the computation of redshift drift along light rays in a simulated universe
based on the Newtonian N-body simulation code GADGET-2 combined with a perturbed
Friedmann-Lemaitre-Robertson-Walker metric in the Newtonian gauge. The study finds that the
mean redshift drift is equal to the drift of the mean redshift to the precision of the numerical
computations, and this is due to a high degree of cancellation between two dominant components
of the redshift drift. The study neglects contributions from optical drift, but it is estimated that
these effects are at most of order 10% of the redshift drift signal. The study also finds that the
redshift drift contribution from peculiar acceleration of the emitter is negligible in the simulation
setup, but it is expected that this contribution will be larger for real observations.
The Generation of Compressed Prompt:
The document discusses the computation of redshift drift in a simulated universe using the Nbody
simulation code GGET2 with a perturbed Lemaître-Lemaître metric. The results show that the
mean shift is equivalent to the shift of the mean to a high degree of cancellation between two
dominant components. This contradicts earlier findings based on inhomogeneous cosmological
models exhibiting cosmic backreaction. The document also estimates that optical drift effects are
at most 10% of the redshift drift signal.
The Generation of Selective-Context:
The document discusses the use of redshift drift measurements, which were first proposed in 1962.
The study involves analyzing non-radial light rays observed by three different observers with
non-radial lines of sight. The present-day density profile of the LTB model is shown in a figure,
and quadri-linear interpolation is used to interpolate between snapshots. The observation time is
set to 30 years, and the mean and fluctuations of the individual components of the redshift drift are
shown in another figure. The study does not address the effects of peculiar acceleration.

 Figure 10: Cases study on Arxiv-March23 summarization dataset in 200 tokens constraint.