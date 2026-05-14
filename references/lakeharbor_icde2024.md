> **Source PDF:** LakeHarbor_Making_Structures_First-Class_Citizens_in_Data_Lakes.pdf
> Extracted with PyMuPDF.

---

<!-- Page 1 -->
 2024 IEEE 40th International Conference on Data Engineering (ICDE)

 LakeHarbor: Making Structures First-Class Citizens
 in Data Lakes

 Hiroyuki Yamada Masaru Kitsuregawa Kazuo Goda
 The University of Tokyo The University of Tokyo The University of Tokyo
 Tokyo, Japan Tokyo, Japan Tokyo, Japan
 hiroyuki@tkl.iis.u-tokyo.ac.jp kitsure@tkl.iis.u-tokyo.ac.jp kgoda@tkl.iis.u-tokyo.ac.jp

 Abstract—This paper introduces LakeHarbor, a new data zens in data lakes. The LakeHarbor paradigm enables a data
 management paradigm that makes structures (e.g., indexes) ﬁrst- lake system to ﬂexibly construct structures based on registered
 class citizens in data lakes. The LakeHarbor paradigm enables access method functions and execute data processing jobs
 a data lake system to ﬂexibly construct structures based on
 efﬁciently with the potential parallelism that the structures in- registered access method functions and execute data processing
 jobs efﬁciently with the potential parallelism that the structures herently hold by exploiting the functions while not sacriﬁcing
 inherently hold by exploiting the functions while not sacriﬁcing the great ﬂexibility of data lakes, e.g., ﬂexible data processing
 ﬂexible data processing such as schema-on-read. This paper with schema-on-read.
 also presents ReDe, a prototype data processing engine that
 implements LakeHarbor, and a motivating evaluation and a case There are several other efforts to increase data process-10.1109/ICDE60146.2024.00446 study of ReDe to explore the potential of LakeHarbor. ing efﬁciency while retaining the ﬂexibility of data lakes.
DOI: I. INTRODUCTION Lakehouse [45] is a data management architecture designed| to take the beneﬁts of both a data warehouse and a data
IEEE Data lakes have been a practical data management archi- lake. Lakehouse aims to improve data processing efﬁciency
 tecture in enterprises to deal with a vast amount of data with by exploiting auxiliary data and optimizing data layout while
 various data models and complex schemas. Data lake systems keeping open ﬁle formats such as Apache Parquet. Self-©2024
 [11], [15] typically hold raw datasets and employ the schema- organizing data container (SDC) [30] provides a storage for-
 on-read processing model or hold datasets in open ﬁle formats mat specialized for cloud-based data lakes. SDC organizes a
 such as Apache Parquet [14] to manage complex data schemas container composed of multiple data ﬁles with rich metadata
 (e.g., nested columns). and adaptively optimizes the data layout of the container to
 Although data lakes achieve great ﬂexibility in managing improve data processing efﬁciency. LakeHarbor is comple-
 various data models and complex schemas, their data pro- mentary to these data management architectures but takes a
 cessing efﬁciency is not necessarily optimized, especially in more drastic approach in terms of exploiting structures and
 selective data processing, due to the conservative exploitation their potential parallelism.
 of structures (e.g., indexes). Enterprises move a curated subset979-8-3503-1715-2/24/$31.00 This paper also presents ReDe, a prototype data processing
| of their data into data warehouses to achieve high performance engine that implements the LakeHarbor paradigm. ReDe pro-
 for demanding structured data access workloads. However, vides an abstraction called Reference-Dereference for deﬁn-(ICDE) the two-tier architecture is complex and causes data freshness ing a data processing job with access methods. Speciﬁcally,
 problems; data in data warehouses is likely to be out of sync Reference-Dereference composes a data processing job with
 with the raw data in data lakes due to the time-consuming a list of sequential reference and dereference functions. The
 extract, transform, and load (ETL) process.Engineering abstraction is based on the fact that a wide variety of data pro- Several approaches [10], [34] exist for creating structures
Data directly on top of data lakes to mitigate such efﬁciency issues cessingfrom a jobsrecordcan(referencing)be expressed andwith obtaininga list of obtaininga recorda frompointera
on while avoiding data freshness problems. These approaches pointer (dereferencing). This abstraction derives the structural
 are designed to process data based on the processing model information of data and the data dependencies about data
 applied in the underlying data lake systems; hence, they accesses, which enable ReDe to construct structures ﬂexibly
 necessarily provide limited capabilities and expressibility forConference and execute data processing jobs efﬁciently with the potential
 structured data accesses. For example, they execute selective parallelism derived from the structures. Consequently, ReDe
 data processing with dozens of statically deﬁned parallelism executes data processing jobs with massive parallelism, which
 (usually matching the number of CPU cores) in each com- could bring signiﬁcant performance improvement, especially
 puting node, and they also cannot execute parallel nestedInternational for selective data processing.
 loop joins with global indexes [38] to improve selective data
40th processing performance. This paper makes the following contributions:
IEEE This paper introduces LakeHarbor, a new data management • We introduce LakeHarbor, a new data management
 paradigm that makes structures (e.g., indexes) ﬁrst-class citi- paradigm that enables a data lake system to ﬂexibly con2024

 2375-026X/24/$31.00 ©2024 IEEE 5583
 DOI 10.1109/ICDE60146.2024.00446
 Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 2 -->
 Application program Application program
 Application program
 Access methods Defined by an abstraction

 Query (SQL) Load Load Load Query Load Load

 Answer Read Read Answer

 Processing engine Processing engine Processing engine Inject

 Access methods Access methods Access methods

 Optimized structured access Unstructured access Optimized structured access
 Limited flexibility High flexibility Improved flexibility

 (a) Data Warehouse (b) Data Lake (c) LakeHarbor

 Fig. 1: Comparison between data management paradigms: data warehouses, data lakes, and LakeHarbor.

 struct structures and execute data processing jobs efﬁciently lakes opt to store given data in a raw form; thus, they remove
 with the potential parallelism derived from the structures the organizing overheads for loading new data in terms of
 without sacriﬁcing the ﬂexible data processing of data lakes. performance and capacity, and interpret the schemas and
 • We also present ReDe, a prototype data processing engine structures of data on the ﬂy on the applications ﬂexibly when
 that implements LakeHarbor, and a motivating evaluation reading the data. In other words, applications deﬁne access
 and a case study of ReDe to explore the potential of methods. Enterprises can invest the reduced cost to extend
 LakeHarbor. the system scalability so that data lakes can offer reasonable
 • We describe a number of research directions for LakeHarbor. performance for expected and unexpected query workloads.
 The remainder of the paper is organized as follows. Section These technical properties are widely accepted by many, often
 II discusses our motivation and vision for LakeHarbor. Sec- burgeoning, enterprises that must be agile and ﬂexible in
 tion III introduces ReDe, a prototype data processing engine responding to changes in their business environment.
 that implements LakeHarbor, and a motivating evaluation of Obviously, the data lake solution misses the technological
 ReDe. Section IV describes a case study of ReDe. Section opportunity that organizing data improves the performance for
V discusses some of the new problems and opportunities expected query workloads. Balanced solutions between the
 that LakeHarbor present. Section VI describes related work. two extremes, data warehouses and data lakes, are actively
 Finally, Section VII concludes the paper. studied. Lakehouse [45] aims to improve data processing
 efﬁciency by exploiting auxiliary data and optimizing data
 II. LAKEHARBOR: MOTIVATION AND VISION layout while keeping open ﬁle formats such as Apache Par-
 Data warehouses (Figure 1(a)) have been a mainstream quet. A self-organizing data container (SDC) [30] organizes a
 solution for enterprises to store and manage large-scale data. container composed of multiple data ﬁles with rich metadata
 Data warehouses organize given data in a form optimized for and adaptively optimizes the data layout of such container to
 expected query workloads. This approach helps to improve improve data processing efﬁciency. Several approaches [10],
 query performance. Instead, it incurs signiﬁcant performance [34] also exist for organizing structures directly on top of
 and capacity overheads for loading new data and sometimes data lakes to improve query performance for expected query
 yields undesirable performance for unexpected workloads. workloads. Although the existing balanced solutions offer
 Data warehouses typically use relational database systems to good performance improvement, they still employ conservative
 store and query data; thus, record schemas (e.g., record format) approaches for dealing with structures in data lakes. For
 and structures (e.g., indexes, logical relationships between example, they execute selective data processing with dozens of
 tables) are pre-deﬁned. In short, the data processing engine statically deﬁned parallelism (usually matching the number of
 of data warehouses deﬁnes access methods based on the CPU cores) in each computing node, and they also cannot
 relational model. This leads to inﬂexible data processing; i.e., execute parallel nested loop joins with global indexes to
 data warehouses cannot efﬁciently store and query data with improve selective data processing performance.
 complex schemas or unstructured data that do not ﬁt well with LakeHarbor (Figure 1(c)) is our balanced solution with a
 the relational model. more drastic approach in terms of exploiting structures in
 Data lakes (Figure 1(b)) are an emerging approach to reduce data lakes. LakeHarbor enables the post hoc deﬁnition of
 the organizing efforts indispensable for data warehouses. Data access methods for data stored in data lakes; the user or

 5584

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 3 -->
 the third-party software is allowed to inject access method ReDe (Compute) reference(Record record)
 Set<Pointer>
 deﬁnitions that describe how one can interpret and access Reference-Dereference Abstraction
 Reference-Dereference Modules reference
 target data. LakeHarbor then creates auxiliary data structures
 (e.g., indexes) for the target data, if necessary, by using
 the deﬁnitions and uses the structures to access the data ReDe Executor
 Pointer Record
 efﬁciently. Since the access method deﬁnitions could contain I/O Abstraction
 arbitrary logic, users can create structures ﬂexibly even on I/O Modules
 complex schemas, such as nested columns, and could also dereference
 exploit the structures when accessing the data by, for example, Storage
 using nested loop joins with global indexes while not spoil- Distributed/ CloudFileStorageSystems(e.g.,(e.g.,S3)HDFS) dereference(Pointer…Set<Record>pointer)
 ing the ﬂexibility properties (e.g., schema-on-read) that data
 lakes hold by nature. Moreover, LakeHarbor obtains detailed (a) Architecture. (b) Reference-Dereference.
 structural information of the stored data through the access
 Fig. 2: ReDe.
 method deﬁnitions; thus, it could execute data processing jobs
 efﬁciently with the potential parallelism that the structures
 inherently hold. Consequently, LakeHarbor could execute data
 records between the ranges that the two pointers point to. The processing jobs with ﬁne-grained massive parallelism, which
 abstraction is based on the fact that a data processing job can could bring signiﬁcant performance improvement, especially
 be expressed with a list of obtaining a pointer from a record for selective data processing. Fine-grained massively parallel
 (referencing) of a ﬁle and obtaining a record of a ﬁle from a execution [17], [28], [43], which fully exploits the parallelism
 pointer (dereferencing) in many cases. of modern hardware that could handle more than thousands
 Reference-Dereference uses I/O abstraction and concrete of concurrent IOs, has been recently explored and validated
 I/O module implementations for the abstraction to access the in relational query processing. LakeHarbor is a solution that
 underlying data of each storage. I/O abstraction deﬁnes three achieves ﬁne-grained massive parallelism in data lakes without
 basic interfaces: Record, Pointer, and File. A Record is a unit compromising the beneﬁts of data lakes.
 of data that ReDe reads and writes. A Pointer is a logical (e.g., We have experienced several real-world workloads that
 record’s primary key) or physical (e.g., ﬁle offset) pointer used LakeHarbor systems would signiﬁcantly beneﬁt, as described
 to locate a Record. A set of Records composes a File. File in Section IV.
 is assumed to be distributed into partitions and can locate a
 III. REDE Record with the corresponding Pointer. Therefore, a Pointer
 also contains partition information to properly locate a Record. This section introduces ReDe, a prototype data process-
 Speciﬁcally, a File takes a partition key from a given Pointer, ing engine (query engine) that implements the LakeHarbor
 applies it to a pre-conﬁgured Partitioner (e.g., HashPartitioner paradigm. There could be many design choices for implement-
 or RangePartitioner) to locates a partition, and locate a Record ing LakeHarbor, and ReDe is one implementation to clarify
 with an in-partition key that can also be taken from the Pointer. the potential of LakeHarbor.
 There is also a special File called BtreeFile. A BtreeFile can
 A. System Overview also locate a set of Records with a range of given Pointers.
 To clarify how Reference-Dereference works, consider a ReDe is a distributed data processing engine designed
 job of join processing for the Part and Lineitem ﬁles of the based on the separation of compute and storage architecture,
 TPC-H dataset. We assume the Part ﬁle is hash-partitioned which has been employed in many recent query engines [3],
 by p partkey and the Lineitem ﬁle is hash-partitioned by [40], [41]. ReDe accepts a job (or a query) written with its
 l orderkey. There are also B-tree indexes on p retailprice abstraction called Reference-Dereference and executes the job
 and l partkey that are hash-partitioned by p partkey and with its executor called ReDe Executor. ReDe Executor uses
 l partkey, respectively. Note that the ﬁles are managed with I/O abstraction, which abstracts underlying storage implemen-
 File, and the B-tree indexes are managed with BtreeFile. The tations, to separate compute and storage. Figure 2(a) shows the
 job corresponds to the following SQL: architecture of ReDe.
 SELECT * FROM Part p JOIN Lineitem l
 B. Reference-Dereference ON p.p_partkey = l.l_partkey
 WHERE p.p_retailprice BETWEEN X AND Y
 Reference-Dereference (Figure 2(b)) is an abstraction for
 deﬁning a data processing job with access methods. Speciﬁ- Figure 3 shows how the join processing can be expressed
 cally, Reference-Dereference composes a data processing job as a list of reference and dereference functions, called Ref-
 with a list of sequential reference and dereference functions. erencers and Dereferencers. Figure 4 shows the (simpliﬁed)
A reference function takes a record and produces a set of Java code of the Referencers and Dereferencers for the join
 pointers to other records that the record is associated with. processing.
A dereference function takes a pointer or two pointers and We ﬁrst look at how the functions obtain a Part record
 produces a set of records that the pointer points to or a set of from its index on p retailprice values. The ﬁrst function

 5585

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 4 -->
 Part Index (BtreeFile) Part (File) Lineitem Index (BtreeFile) Lineitem (File)
 (partitioned by partkey) (partitioned by partkey) (partitioned by partkey) (partitioned by orderkey)

 Pointers Referencer-1 Referencer-2 Referencer-3
 (p_retailprice)
 Pointers Pointers Pointers

 Dereferencer-0 Dereferencer-1 Dereferencer-2 Dereferencer-3
 (Local) (Local) (Local) (Remote)

 Fig. 3: Example Referencers and Dereferencers for Part-Lineitem join.

 A Job Program for REDE (user-defined) Referencer-1/3 (pre-defined by the system) Referencer-2 (pre-defined by the system)

 ++ ) ) + - ++ ) ) + -
 . - . -
 !" #" $$ % $$ + / ) 7 7
 & ' $$ !
 $$ ! , 8 ' , 8 '
 $$ ( , 8 ' , 8 ' )
 ) & ' $$ ( ) 6
 * ) & ' $$ # 6 6
 ) $$ # 6
 Dereferencer-2 (pre-defined by the system)
 Dereferencer-0 (pre-defined by the system) Dereferencer-1/3 (pre-defined by the system)
 ++ ) ) + -
 ++ ) ) + , - ++ ) ) + - . -
 . ,/ - . - 0 1 ,
 0 1 , ,/ , 2/ 3 / +4 -
 2/ 3 / +4 - ) / -
 $$ ) ' ) / , + ) / -
 ) / - $$ 5 '+ ) / + 5 + 5 ) )
 ) 6 6
 6 6 6
 66 6 66

 Interpreter for Referencer-1 (user-defined) Interpreter for Referencer-2 (user-defined) Interpreter for Referencer-3 (user-defined)

 ++ & ' ) ) + - ++ ) ) + - ++ * ) & ' ) ) + -
 - - -
 $$ ' $$ $$ ' * )
 66 66 66

 Fig. 4: Code of Referencers and Dereferencers for Part-Lineitem join.

 Dereferencer-0 takes a range of Part.p retailprice values as Lineitem.l partkey. Dereferencer-2 takes the pointer and uses
 arguments and uses the B-tree index to get a set of matching the B-tree index to get a set of matching records. Referencer-
 records. Note that every Dereferencer manages either a File or 3, the same code as Referencer-1, creates a pointer to the
 a BtreeFile to access. In this example, Dereferencer-0 manages Lineitem ﬁle in the same way. Dereferencer-3, which is
 a local secondary B-tree index of the Part ﬁle, and the obtained the same code as Dereferencer-1, accesses the Lineitem ﬁle
 records consist of logical pointers of the Part ﬁle. It then emits using the pointer. Note that the last dereferencing fetches the
 each record if the record matches a ﬁltering condition. The Lineitem records through cross-partition accesses since the
 ﬁltering condition is optionally provided at the time of job index for l partkey and Lineitem are partitioned by different
 deﬁnition with a function called Filter, which interprets a given keys.
 record with schema-on-read and ﬁlters out the record if the A ReDe job deﬁnes a list of the reference and dereference
 given condition does not match the record.1 Following that, functions, as described in Figure 4. Composing such a list is
 the second function Referencer-1 takes the record that was similar to creating a MapReduce [6] job caring for how data
 emitted in the previous dereference function. It also interprets is partitioned.
 the schema of the record with a function called Interpreter ,
 The Reference-Dereference abstraction is conceptually sim-
 which interprets a given record with schema-on-read. It then
 ilar to Skywriting in CIEL [31], [32], which manages data
 creates a pointer to a Part record from the interpreted record
 dependencies of data by applying a dereference operator to a
 and emits the pointer. As we explained, the pointer is not
 data reference in a program. However, Skywriting is designed
 necessarily pointing to a local ﬁle; thus, it has a partition key
 for managing coarse-grained data such as ﬁles; thus, it cannot
 to locate a node. The third function Dereferencer-1 takes the
 extract the structures of data, i.e., it cannot exploit the ﬁne-
 pointer and accesses the Part ﬁle using the pointer to get the
 grained parallelism of the data. On the other hand, Reference-
 corresponding record.
 Dereference is designed to extract ﬁne-grained data paral-
 Next, we look at how the functions obtain a Lineitem
 lelism, which could bring signiﬁcant performance beneﬁts, as
 record from the obtained Part record. Referencer-2 takes the
 discussed in Section III-C.
 Part record and extracts a pointer to the B-tree index of
 Although the abstraction seems to offer a limited or low-
 1No ﬁlter is given in the example for ease of explanation. level programming interface, we believe it is still suitable for

 5586

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 5 -->
 a broad class of applications and not very difﬁcult to manage
 for programmers.
 Expressibility. Reference-Dereference can express a wide
 range of index-based structured data processing such as se-
 lection and join. Speciﬁcally, the current design supports data
 processing using the indexing schemes deﬁned in a taxonomy
 paper [38]. For example, as described in Figure 4, it can
 express parallel index nested loop joins whether or not the
 used indexes are local or global. Moreover, it can express
 broadcast joins, where index pointers are broadcasted to all the
 partitions. Speciﬁcally, the broadcast joins can be expressed
 by passing a null value to the partition information of the
 pointer emitted by a Referencer, which makes the system Fig. 5: Example of ReDe execution for nested loop joins.
 replicate the given pointer to all the partitions. Multi-way joins
 can also be naturally expressed by appending Referencers and
 Dereferencers. in the ﬁgure). When fetching the records of Part with the
 Usability. Referencers and Dereferencers to support the in- pointers with Dereferencer-1, ReDe creates a thread for each
 dexing schemes [38] are pre-deﬁned by the system and dereference function invocation and uses the thread to fetch the
 reusable. For example, all the Referencers and Dereferencers corresponding record of Part by exploiting the independence
 in Figure 4 are pre-deﬁned except for Interpreters. Therefore, of record accesses. For each fetched record of Part, it can
 programmers’ task to deﬁne a job in most cases is choosing extract the foreign key attribute with Referencer-2 to access
 Referencers and Dereferencers to use, creating an Interpreter Lineitem. The extraction for multiple records could also be
 for each Referencer for schema-on-read, optionally creating done in parallel by creating a thread for each referencer
 a Filter for each Dereferencer, and composing a list of function invocation.
 Referencers and Dereferencers that makes sense as a data Once foreign keys are extracted, it accesses the index of
 processing, which is similar to creating a MapReduce job Lineitem with Dereferencer-2. Since the accesses are inde-
 caring for how data is partitioned. Even if programmers want pendent as well, multiple dereference function invocations can
 to create complex Referencers and Dereferencers that are not be executed in parallel in the same way. Here we assume
 pre-deﬁned, they need to create them only for each ﬁle, not for that it produces more pointers than the number of foreign
 each job, since the functions are not speciﬁc to jobs, as seen keys due to a fanout (each key producing two pointers in the
 in Figure 4. Thus, we believe that Reference-Dereference does ﬁgure; thus, six pointers). Then, for each obtained pointer,
 not give too much burden to programmers, and programmers each node accesses Lineitem with Dereferencer-3. Similarly,
 who have experience in MapReduce or Spark can create a these accesses can be done in parallel whether or not they are
 ReDe job without much difﬁculty. local accesses or remote accesses. As illustrated in Figure 5,
 all the nodes execute the data processing job in the same way.
 C. Optimizing Execution Efﬁciency
 As can be seen, the job has a lot more parallelism than
 The Reference-Dereference abstraction helps to derive the the partitioned parallelism given by the three nodes. ReDe
 structural information of the data accessed by a job and the potentially exploits the parallelism derived from a given job
 data dependencies about the data accesses. ReDe leverages the and the underlying nodes’ hardware capacity, such as IOPS,
 information and data dependencies to dynamically decompose at full. Note that we explain the approach in a simpliﬁed
 a job into ﬁne-grained tasks during job execution and achieves form; however, there will be more opportunities for parallel
 scalable massively parallel execution (SMPE) [43] in data processing. For example, when a data processing job is N-way
 lakes. join where N is bigger than two, it could execute with more
 To clarify how ReDe’s SMPE works, consider the same parallelism because it accesses more records.
 job (query) described in the previous section in a three-node Figure 6 shows the execution model of ReDe for achieving
 cluster.2 Figure 5 illustrates the simpliﬁed form of ReDe SMPE and Algorithm 1 shows the SMPE algorithm based on
 execution for nested loop joins between Part and Lineitem. the model. ReDe divides a data processing job into multiple
 ReDe is initiated by distributing the data processing job stages and executes one of the given functions (i.e., Referencer
 to all the computing nodes. For each node, it ﬁrst retrieves and Dereferencer) in each stage. Each stage has an input queue
 a pointer given from the job and passes it to Dereferencer- and an output queue, and the output queue of one stage is the
 0. Assuming that there is a certain degree of cardinality in input queue of the next stage.
 the index and the condition predicate matches with several As discussed, ReDe is initiated by distributing a data
 distinct values; thus, it emits multiple pointers of Part (three processing job to all the computing nodes (lines 2-5), and
 each node executes the stages of the job (lines 8-18). In
 2We explain the example with a shared-nothing cluster for ease of expla-
 nation, but ReDe works the same even on a shared disk such as S3. each node, SMPE takes the Dereferencer function of the

 5587

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 6 -->
 Algorithm 1 Scalable Massively Parallel Execution in ReDe.

 1: function EXECUTESMPE(job, nodes)
 2: for i ←1, length(nodes) do
 3: // invocation returns immediately before completion
 4: INVOKE(nodes[i], EXECUTESMPEEACH)
 5: end for
 6: Wait until all the execution is completed
 7: end function
 Fig. 6: Execution model of ReDe for SMPE. 8: function EXECUTESMPEEACH(job)
 9: queue ←CREATEQUEUE
 10: // the order of funcs speciﬁes data dependencies,
 11: // and funcs deﬁne structural information
 initial stage, executes the function (lines 14, 19-24, and 45), 12: funcs ←GETFUNCTIONS(job)
and puts the emitted outputs of the function into the queue 13: t1 ←CREATETHREAD
 (lines 47-51). Note that ReDe executes the initial stage and 14: EXECUTEINITIALSTAGE(funcs, queue) on t1
 15: t2 ←CREATETHREAD
 the Dereferencer function on different threads from the main 16: EXECUTESTAGES(funcs, queue) on t2
 thread not to block the execution of other stages and functions. 17: Wait until the execution on the threads are completed
 Then, ReDe creates threads and executes the other Referencer 18: end function
and Dereferencer functions of the subsequent stages on the 19: function EXECUTEINITIALSTAGE(funcs, queue)
 20: stage ←0
 threads whenever it detects data (i.e., a pointer or a record) 21: func ←funcs[stage] ▷func is the initial Dereferencer
 in the queue (lines 16, 25-42). Speciﬁcally, it creates two 22: input ←GETINPUT(func)
 threads for each data from the queue, and dispatches one to 23: EXECUTEFUNC(func, input, queue)
 24: end function
 execute a function (lines 44-45) and dispatches the other to 25: function EXECUTESTAGES(funcs, queue)
 handle the emitted outputs of the function (lines 47-51) so 26: while until all tasks are ﬁnished do
 that executing the function does not block the execution of 27: input ←DEQUE(queue)
 28: if input does not have partition information then other stages and functions. Consequently, ReDe executes the 29: SETPARTITION(input, LOCAL)
 functions massively in parallel. 30: BROADCAST(input)
 In the current implementation, ReDe manages threads in a 31: ▷enque input to all the nodes’ queues
 32: continue thread pool and reuses them instead of creating them every 33: end if
 time. It manages 1000 threads in the default setting, but 34: func ←funcs[input.stage]
 the number can be adjusted based on underlying hardware 35: ▷func is Referencer or Dereferencer
 36: if func is null then capabilities such as the number of CPU cores and the IOPS of 37: continue
IO path. Moreover, as an optimization, ReDe does not switch 38: end if
 threads for Referencers by default to avoid excessive context 39: t ←CREATETHREAD ▷create if func is Dereferencer
 40: EXECUTEFUNC(func, input, queue) on t switching because Referencers do not usually incur IO and are 41: end while
 lightweight. 42: end function
 43: function EXECUTEFUNC(func, input, queue)
D. Structure Maintenance 44: t ←CREATETHREAD ▷create if func is Dereferencer
 ReDe builds indexes ﬂexibly in the background by using 45: func(input) on t ▷outputs are pushed to emitted
 46: ▷func might fetch data from remote nodes
 registered Interpreters and Referencers. An Interpreter for a 47: while until all emitted results are processed do
 File extracts a partition key and an index key in the partition 48: output ←DEQUE(emitted)
from each record, and a Referencer emits a pair of the partition 49: new input ←CREATEINPUT(output, input.stage+1)
 50: ENQUE(queue, new input)
key and the index key for the record. Then, ReDe lazily creates 51: end while
 indexes by using the emitted pair. With the mechanism, ReDe 52: end function
 provides a ﬂexible indexing scheme even on complex schemas,
 as described in Section IV.
 For the data drive, we set noop for IO scheduler and 1008
 E. Preliminary Evaluation
 for nr request and queue depth parameters.The nodes were
 This section evaluates one of the beneﬁts of ReDe, the connected with Dell Force10 Z9000 10 Gbps switch.
 efﬁciency of structured data processing, to clarify the potential As a preliminary evaluation, we compared ReDe with a
 of LakeHarbor. fast data lake system, Apache Impala [13]. Impala is a query
Environment. We ran our experiments on a 128-node cluster. engine focusing on analytical workloads and not supporting
Each node was a 2U server that was equipped with two Intel indexes. We used Impala version 3.0. In future work, we will
Xeon E5-2680 2.70 GHz processors (16 cores in total), 64 conduct more experiments with other query engines, such as
GB memory, two 15K RPM 300 GB SAS HDDs for OS Spark [15], Photon [3], and Snowﬂake [41].
(OS drives), and twenty-four 10K RPM 900 GB SAS HDDs Dataset. We used TPC-H dataset [5] for the experiments. We
 for data (data drives). We created a RAID-6 array across generated ﬁles with SF=128K. The total size of all the ﬁles
 the twenty-four HDDs using the attached RAID controller. was about 128TB. We created a HDFS [11] cluster using the
Each node ran CentOS Linux and used ext4 for both drives. data drives of the nodes and loaded the dataset into the HDFS

 5588

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 7 -->
 105
 Record (piecework claim)
 (s) 104 IR type hospital ID
 time 103 RE in/outpatient patient ID gender age
 HO expenses
 102 SI treatment expenses times Execution 101 Data lake system (Impala) SI treatment expenses times
 LakeHarbor system (ReDe w/o SMPE)
 LakeHarbor system (ReDe w/ SMPE) IY medicine amount expenses
 100
 0.001 0.01 0.1 1 10 100 IY medicine amount expenses
 Selectivity (%) SY disease
 Fig. 7: Performance comparison between a data lake system SY disease
 and a LakeHarbor system (ReDe).

 Record (DPC claim)
 cluster where the dataset was distributed into the nodes by IR type
 round-robin.
 For ReDe, we created a simple distributed ﬁle system for
 the experiments and used it instead of HDFS since HDFS
 is not well-optimized for non-scan accesses such as lookups.
 Fig. 8: Example of Japanese insurance claims.
 We loaded the ﬁles into the distributed ﬁle system, which
 distributed the ﬁles into 128 partitions evenly spread into the
 nodes by hashing with their primary keys. We also created
 local secondary indexes on the date columns (e.g., o orderdate IV. CASE STUDY
 in Order) of each ﬁle and global indexes for each foreign key In this section, we introduce a use case of ReDe. As
 of each ﬁle. Each global index is also distributed into partitions discussed in Section II, the primary beneﬁt of data lake is
 by the corresponding foreign key. its ﬂexibility in data formats. In contrast to data warehouses,
 Workload. We used a simpliﬁed TPC-H query (TPC-H Q5’), which store data according to pre-deﬁned schemas based on
 which is a variant of the TPC-H Q5 query, where the sorting the relational model, data lakes do not pre-deﬁne the schemas
 and aggregation are removed to focus on clarifying the perfor- of data and let the applications interpret them on the ﬂy when
 mance differences for a SPJ (select-project-join) workload. We reading the data. ReDe inherits the beneﬁt of data lakes while
 also varied the selectivities of the query using the predicates to making it possible to specify access method deﬁnitions in a
 cover a wide selectivity range to see how the systems behave post hoc manner. The access method deﬁnitions enable ReDe
 for each selectivity. to obtain detailed structural information of stored data; thus,
 Evaluation Results. Figure 7 shows the evaluation results. ReDe could execute data processing jobs efﬁciently with the
 Impala executed the query using (grace) hash joins; the potential parallelism that the data inherently holds.
 execution time of Impala gradually increased as the selectivity Let us dive into a case of the analytics of public healthcare
 increased. On the other hand, ReDe executed the query using insurance claims in Japan. Similar to the UK and Germany,
 Referencers and Dereferencers (i.e., parallel nested loop joins); Japan has employed the universal service policy; publicly
 the execution time of ReDe increased more steeply as the operated healthcare insurance programs cover all necessary
 selectivity increased. ReDe (w/o SMPE) simply used the medical care for all citizens and compensate for most med-
 created structures and the partitioned parallelism given from ical expenses. Insurance claims are digitally managed and
 data partitions; thus, it showed a slight performance beneﬁt exchanged between medical institutions and public insurers,
 over Impala in the very low selectivity range. By contrast, each describing medical expenses charged to a patient and its
 ReDe (w/ SMPE) outperformed Impala by more than an order evidential information, such as diagnosed diseases and medical
 of magnitude in a wide range of selectivities because of scal- services (e.g., prescriptions and treatments) provided to the
 able massively parallel execution, which effectively exploited patient. The nationwide collective database of these insurance
 the derived structural information of data accesses and the claims has a strong potential to gain a broad spectrum of new
 data dependencies about the data accesses, as explained in ﬁndings on medical policies and medical technologies.
 Section III-C. Note that ReDe became slower than Impala The data format of the insurance claims, standardized by
 in the high selectivity range because the current prototype the government, contains a high structural complexity to
 does not implement efﬁcient data processing on unstructured describe various clinical situations, as shown in Figure 8.
 data or a query optimizer. If ReDe implements them, ReDe The insurance claims ﬁle is a text comprised of multiple
 could choose data processing plans appropriately based on records, each comprising multiple sub-records of different
 query selectivities; i.e., ReDe would perform comparably with kinds. The format of each sub-record is determined by the
 Impala in the high selectivity range. two leading characters. ”IR” indicates a record describing a

 5589

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 8 -->
 hospital claiming the medical expenses. The type attribute of DataLakeHarborwarehousesystemsystem(ReDe)
 an IR sub-record speciﬁes if the record is a piecework or a
DPC claim; hence, the records are dynamically deﬁned. A 1 accesses
”RE” sub-record describes a service category (e.g., in-patient 0.8
 or out-patient) and patient information. A ”HO” sub-record record 0.6
 describes total medical expenses. ”SI”, ”IY”, and ”SY” sub- of# 0.4
 records describe medical treatments provided to the patient,
 medicines prescribed to the patient, and diseases diagnosed to 0.2 the patient, respectively. Normalized 0
 We tried two major approaches to analyzing the insurance Q1 Q2 Q3
 claims: (1) normalizing the data based on the relational model Fig. 9: Differences in the number of record accesses between
 and storing it in a data warehouse system that employs ﬁne- a data warehouse system that employs ﬁne-grained massively
 grained massively parallel execution [17], and (2) storing it in parallel execution and a LakeHarbor system (ReDe). The
 a raw form in a data lake system. The ﬁrst approach yielded numbers are normalized based on the number of the data
 performance penalties due to intensive joins of normalized warehouse system.
 data even though ﬁne-grained massively parallel execution
 helped to improve overall performance. The second approach
 provided slow performance due to a full data scan with the
 to healthcare researchers [20], [21], [23]–[25], [35], [36], [39],
 statically deﬁned parallelism based on the data lake system.
 [42].
 Moreover, the second approach could not utilize nested-
 This section has focused on a case of Japanese healthcare
 column ﬁle formats such as Apache Parquet [14] because such
 insurance analytics, but ReDe could be applied to other cases.
 ﬁle formats cannot properly express the dynamically deﬁned
 The international medical community has recently promoted
 records of the insurance claims.
 FHIR [16], the format standard of electronic medical records.
 We then came up with ReDe as our solution, which stores
 FHIR has a similar design to the Japanese insurance claims
 insurance claims in raw form in storage and deﬁnes how the
 format, employing the nested record organization. We expect
 data is accessed. ReDe performed signiﬁcantly better than the
 ReDe would also manage and process the FHIR data ﬂexibly
 other systems by eliminating the performance overhead of
 and efﬁciently.
 joins while executing queries with massive parallelism.
 The performance differences between ReDe and the data V. RESEARCH DIRECTIONS
 warehouse system mainly came from the differences in the So far, we described the high level idea of LakeHarbor and
 number of record accesses. That is because both systems its example implementation (ReDe) to verify the potential of
 accessed their stored data with ﬁne-grained massively parallel LakeHarbor. In this section, we focus on some of the new
 execution, and the number of record accesses determines the problems and opportunities that LakeHarbor present.
 theoretical limitation of query performance in those systems.
 Figure 9 shows the normalized numbers of record accesses of A. Abstraction
 these systems for the following three queries:3 We introduced one example of systems that support LakeQ1 Calculate medical expenses charged to medical care pre- Harbor, but there could be a system that better supports the
 scribing antihypertensive medicines for hypertension. paradigm. For example, the Reference-Dereference abstraction
Q2 Calculate medical expenses charged to medical care pre- of ReDe can fully exploit structures and provide ﬂexible and
 scribing antimicrobial medicines to acne patients. efﬁcient data processing; however, as a trade-off, it might not
Q3 Calculate medical expenses charged to medical care pre- be high-level enough. A higher-level abstraction brings not
 scribing GLP-1 receptor medicines to diabetes patients. only better usability but also an opportunity for query opti-
 mizations, which could help to select appropriate structures forThe results show that while ReDe executed queries with ﬁne-
 efﬁcient data processing. Exploring higher-level abstractions grained massively parallel execution, it accessed signiﬁcantly
 without compromising ﬂexibility and efﬁciency is an important fewer records because its ﬂexible data processing with schema-
 research challenge. on-read avoided the intensive joins caused by data normaliza-
 tion. B. Structure Maintenance
 ReDe has been running in a real research platform for Although we have discussed the concept of registering
 healthcare analyses by an interdisciplinary team between com- access methods and lazily building structures using the access
 puter science and healthcare in Japan [18]. Speciﬁcally, it methods, we have not discussed what structures to build
 has been employed as a data analytics infrastructure of the and at what times. It is essential to clarify those to build a
 research platform to analyze the nationwide insurance claims practical LakeHarbor system, and we believe the following
 database and has provided an efﬁcient data processing service should be considered. First, having many structures could
 provide more opportunities to derive more efﬁcient structured 3We omitted the result of the data lake system because it was a lot slower
 than the others. data processing; however, more structures could cause more

 5590

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 9 -->
 performance and capacity overheads for loading new data. in these systems is determined by the number of partitions,
 Therefore, we should care about data processing performance which is also usually statically deﬁned. ReDe employs a ﬁner-
 and loading performance to decide what structures to build. grained dynamic parallel execution method, and we focused
 Second, workloads are not static in recent analytics, so struc- on introducing a way to apply the method to data lakes.
 ture maintenance should be adaptive to workload changes and
 future workloads. C. Fine-grained Parallel Execution of Data Processing
 Recent work uses ﬁner-grained task decomposition and dis-
 C. Storage Engine
 tribution to fully take advantage of modern hardware capabili-
 Since systems for LakeHarbor fully exploit the parallelism ties. Morsel-driven query execution [28] takes small fragments
 of structures, their data access workloads could be more ﬁne- of input data (morsels) and schedules these to worker threads
 grained than the ones of existing systems for data lakes and that run entire operator pipelines at run-time to fully exploit the
 Lakehouses. Emerging storage engines such as Delta Lake parallelism of many-core architecture. Out-of-order database
 [2] and Apache Iceberg [12] are promising approaches but execution [17] and scalable massively parallel execution [43]
 not likely to be optimized enough for such workload. It is dynamically decompose query work during query execution
 worth exploring a new storage layer for better efﬁciency in to fully exploit the parallelism of underlying storage devices.
 the LakeHarbor workload. The execution model of ReDe inherits the philosophy of the
 above work, but we focused on introducing a way to apply D. Integration with Existing Systems
 the above work to data lakes.
 LakeHarbor is not an exclusive paradigm and could coexist
 with other data management paradigms, such as Lakehouse. D. Modern Query Engines for Data Lakes
 Therefore, integrating a LakeHarbor system and a Lakehouse
 Modern query engines for data lakes focus on exploiting
 system might be a practical and promising approach to achieve
 CPUs efﬁciently and follow one of two designs: either an inter-
 a system that gets the best of both worlds. This architecture
 preted vectorized design like in Photon [3] or a code-generated
 opens up a new design space. First, how to integrate multiple
 design like in HyPer [33] and Apache Impala [13]. ReDe takes
 systems based on different paradigms is challenging. Second,
 a drastically different approach from these two designs to focus
 even if multiple systems are integrated seamlessly, it is chal-
 on optimizing I/O accesses by exploiting structures in data
 lenging to optimize a query to derive an execution plan that
 lake systems. This approach could be complementary to the
 best utilizes the beneﬁts of all the systems.
 approaches of such modern query engines; however, exploring
 VI. RELATED WORK how we can seamlessly integrate both approaches is our future
 work. A. Abstractions for Processing Large-scale Data
 MapReduce [6], Dryad [22], Spark [44], and CIEL [32] VII. CONCLUSION
 provide general-purpose abstractions (or programming mod-
 We presented our vision for a new data management els) for processing large-scale data. Although these provide
 paradigm called LakeHarbor, which makes structures (e.g., different abstractions, they are designed for achieving coarse-
 indexes) ﬁrst-class citizens in data lakes. LakeHarbor is yet grained parallelism. ReDe is one of the abstractions for
 another balanced solution for achieving the ﬂexibility of LakeHarbor and is speciﬁcally designed for exploiting the
 data lakes and the performance of data warehouses with a structures of data and their potential ﬁne-grained parallelism.
 special focus on structured data processing that fully exploits
 B. Parallel Execution of Data Processing the parallelism of data. We also explored the potential of
 LakeHarbor by presenting ReDe, a prototype data processing There has been a lot of work to exploit intra-query par-
 engine that efﬁciently supports LakeHarbor, and a motivating allelism in parallel database systems. Pipelined parallel ex-
 evaluation and a case study of ReDe. We believe that the ecution [4], [8], [19], [29], [37], [46], in which operators
 ﬁndings explored in this paper are beneﬁcial for creating next- work in series by streaming their output to the input of the
 generation data processing systems on data lakes. next one, is widely applied in database systems to exploit
 pipelined (vertical) parallelism. This approach was also ex-
 ACKNOWLEDGMENT
 tensively researched, especially in hash join algorithms in
 parallel database systems. These systems typically exhibit This work has been partially supported by FIRST and
 a small amount of parallelism and use a ﬁxed degree of ImPACT research funding programs of Cabinet Ofﬁce, Japan,
 parallelism, which are usually determined by the number of Cross-cutting Technology Development for IoT Promotion
 operators of a query. Partitioned parallel execution [1], [7]– program of NEDO and Big Data Value Co-creation Platform
 [9], [26], [27], in which input data is logically or physically Engineering social cooperation program at UTokyo-IIS with
 partitioned into one or more nodes and operators are split Hitachi, and Cross-ministerial Strategic Innovation Promotion
 into many independent ones working on the part of data, is Program (SIP) on Integrated Health Care System. We are also
 also widely applied in parallel database systems to exploit grateful to Naohiro Mitsutake and Hiromasa Yoshimoto, who
 partitioned (horizontal) parallelism. The amount of parallelism helped us conduct the case study appropriately.

 5591

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.

<!-- Page 10 -->
 REFERENCES [24] T. Ishikawa, J. Sato, J. Hattori, K. Goda, M. Kitsuregawa, and N. Mit-
 sutake. Changes in demand volume and patient/health care provider
 [1] MC. Albutiu, A. Kemper, and T. Neumann. Massively Parallel Sort- characteristics of ﬁrst-time telehealth users: A comparative analysis
 merge Joins in Main Memory Multi-core Database Systems. PVLDB, before and after the COVID-19 policy response using the administrative
 5(10):1064–1075, June 2012. claims database. Telemed. J. E. Health., August 2023.
 [2] M. Armbrust, T. Das, L. Sun, B. Yavuz, S. Zhu, M. Murthy, J. Torres, [25] N. Kanda, H. Hashimoto, T. Imai, H. Yoshimoto, K. Goda, N. Mitsutake,
 H van Hovell, A. Ionescu, A. Łuszczak, M. ´Switakowski, M. Szafra´nski, and S. Hatakeyama. Indirect impact of the covid-19 pandemic on the
 X. Li, T. Ueshin, M. Mokhtar, P. Boncz, A. Ghodsi, S. Paranjpye, incidence of non-covid-19 infectious diseases: a region-wide, patient-
 P. Senster, R. Xin, and M. Zaharia. Delta Lake: High-Performance ACID based database study in japan. Public Health, 2022.
 Table Storage over Cloud Object Stores. PVLDB, 13(12):3411–3424, [26] C. Kim, T. Kaldewey, VW. Lee, E. Sedlar, AD. Nguyen, N. Satish,
 2020. J. Chhugani, A. Di Blas, and P. Dubey. Sort vs. Hash Revisited: Fast
 [3] A. Behm, S. Palkar, U. Agarwal, T. Armstrong, D. Cashman, A. Dave, Join Implementation on Modern Multi-core CPUs. PVLDB, 2(2):1378–
 T. Greenstein, S. Hovsepian, R. Johnson, A. Sai Krishnan, P. Leventis, 1389, August 2009.
 A. Luszczak, P. Menon, M. Mokhtar, G. Pang, S. Paranjpye, G. Rahn, [27] M. Kitsuregawa, H. Tanaka, and T. Moto-oka. Application of Hash to
 B. Samwel, T. van Bussel, H. van Hovell, M. Xue, R. Xin, and Database Machine and Its Architecture. 1(1):63–74, 1983.
 M. Zaharia. Photon: A Fast Query Engine for Lakehouse Systems. [28] V. Leis, P. Boncz, A. Kemper, and T. Neumann. Morsel-driven Paral-
 In SIGMOD, page 2326–2339, 2022. lelism: A NUMA-aware Query Evaluation Framework for the Many-core
 [4] MS Chen, ML Lo, PS. Yu, and HC. Young. Using Segmented Right- Age. In SIGMOD, pages 743–754, 2014.
 Deep Trees for the Execution of Pipelined Hash Joins. In VLDB, pages [29] B. Liu and EA. Rundensteiner. Revisiting Pipelined Parallelism in Multi-
 15–26, 1992. Join Query Processing. In VLDB, pages 829–840, 2005.
 [5] Transaction Processing Performance Council. TPC-H is a Decision [30] S. Madden, J. Ding, T. Kraska, S. Sudhir, D. Cohen, T. Mattson, and
 Support Benchmark. http://www.tpc.org/tpch/, 2023. N. Tatbul. Self-organizing data containers. In CIDR, 2022.
 [6] J. Dean and S. Ghemawat. MapReduce: Simpliﬁed Data Processing on [31] DG. Murray and S. Hand. Scripting the Cloud with Skywriting. In
 Large Clusters. In OSDI, pages 137–149, 2004. HotCloud, 2010.
 [7] D. DeWitt. DIRECT - a Multiprocessor Organization for Supporting [32] DG. Murray, M. Schwarzkopf, C. Smowton, S. Smith, A. Mad-
 Relational Data Base Management Systems. In ISCA, pages 182–189, havapeddy, and S. Hand. CIEL: A Universal Execution Engine for
 1978. Distributed Data-Flow Computing. In NSDI, page 113–126, 2011.
 [8] D. DeWitt and J. Gray. Parallel Database Systems: The Future of High [33] T. Neumann. Efﬁciently Compiling Efﬁcient Query Plans for Modern
 Performance Database Systems. Commun. ACM, 35(6):85–98, June Hardware. PVLDB, 4(9):539–550, 2011.
 1992. [34] R. Potharaju, T. Kim, E. Song, W. Wu, L. Novik, A. Dave, A. Fogarty,
 [9] D.J. DeWitt, J.F. Naughton, and J. Burger. Nested Loops Revisited. P. Pirzadeh, V. Acharya, G. Dhody, J. Li, S. Ramanujam, N. Bruno,
 In Proceedings of the Second International Conference on Parallel and C. Galindo-Legaria, V. Narasayya, S. Chaudhuri, A. Nori, T. Talius,
 Distributed Information Systems, pages 230–242, 1993. and R. Ramakrishnan. Hyperspace: The Indexing Subsystem of Azure
 [10] J. Dittrich, J. Quian´e-Ruiz, A. Jindal, Y. Kargin, V. Setty, and J. Schad. Synapse. 14(12), 2021.
 Hadoop++: Making a Yellow Elephant Run like a Cheetah (without It [35] J. Sato, N. Mitsutake, M. Kitsuregawa, T. Ishikawa, and K. Goda. Pre-
 Even Noticing). PVLDB, 3(1–2):515–529, 2010. dicting demand for long-term care using japanese healthcare insurance
 [11] Apache Software Foundation. Apache Hadoop. https://hadoop.apache. claims data. Environ. Health Prev. Med., 27(0):42, 2022.
 org/, 2024. [36] J. Sato, N. Mitsutake, H. Yamada, M. Kitsuregawa, and K. Goda.
 [12] Apache Software Foundation. Apache Iceberg. https://iceberg.apache. Virtual patient identiﬁer (vPID): Improving patient traceability using
 org/, 2024. anonymized identiﬁers in Japanese healthcare insurance claims database.
 [13] Apache Software Foundation. Apache Impala. https://impala.apache. Heliyon, 9(5):e16209, 2023.
 org/, 2024. [37] Donovan A. Schneider and David J. DeWitt. Tradeoffs in Processing
 [14] Apache Software Foundation. Apache Parquet. https://parquet.apache. Complex Join Queries via Hashing in Multiprocessor Database Ma-
 org/, 2024. chines. In VLDB, pages 469–480, 1990.
 [15] Apache Software Foundation. Apache Spark. https://spark.apache.org/, [38] D. Taniar and J Rahayu. A Taxonomy of Indexing Schemes for Parallel
 2024. Database Systems. Distributed and Parallel Databases, 12(1):73–106,
 [16] HL7 FHIR Foundation. HL7 FHIR Foundation Enabling health inter- 2002.
 operability through FHIR. https://fhir.org/, 2024. [39] R. Tsunoda, N. Mitsutake, T. Ishikawa, J. Sato, K. Goda, N. Nakashima,
 [17] K. Goda, Y. Hayamizu, H. Yamada, and M. Kitsuregawa. Out-of-Order M. Kitsuregawa, and K. Yamagata. Monthly trends and seasonality
 Execution of Database Queries. PVLDB, 13(12):3489–3501, 2020. of hemodialysis treatment and outcomes of newly initiated patients
 [18] K. Goda and M. Kitsuregawa. Powerful Analytics Platform for National- from the national database (NDB) of Japan. Clinical and Experimental
 Scale Database of Health Care Insurance Claims, pages 29–31. Springer Nephrology, 26(7):669–677, July 2022.
 Nature Singapore, 2022. [40] A. Verbitski, A. Gupta, D. Saha, M. Brahmadesam, K. Gupta, R. Mittal,
 [19] S. Harizopoulos, V. Shkapenyuk, and A. Ailamaki. QPipe: A Simultane- S. Krishnamurthy, S. Maurice, T. Kharatishvili, and X. Bao. Amazon
 ously Pipelined Relational Query Engine. In SIGMOD, pages 383–394, Aurora: Design Considerations for High Throughput Cloud-Native Re-
 2005. lational Databases. In SIGMOD, page 1041–1052, 2017.
 [20] H. Hashimoto, M. Saito, J. Sato, K. Goda, N. Mitsutake, M. Kitsure- [41] M. Vuppalapati, J. Miron, R. Agarwal, D. Truong, A. Motivala, and
 gawa, R. Nagai, and S. Hatakeyama. Indications and classes of outpatient T. Cruanes. Building An Elastic Query Engine on Disaggregated
 antibiotic prescriptions in Japan: A descriptive study using the national Storage. In NSDI, pages 449–462, 2020.
 database of electronic health insurance claims, 2012–2015. International [42] T. Waki, K. Miura, S. Mizuno-Tanaka, Y. Ohya, K. Node, H. Itoh,
 Journal of Infectious Diseases, 91:1–8, 2020. H. Rakugi, J. Sato, K. Goda, M. Kitsuregawa, T. Ishikawa, and N. Mit-
 [21] K. Hirayama, N. Kanda, H. Hashimoto, H. Yoshimoto, K. Goda, sutake. Prevalence of Hypertensive Diseases and Treated Hypertensive
 N. Mitsutake, and S. Hatakeyama. The ﬁve-year trends in antibiotic Patients in Japan: A Nationwide Administrative Claims Database Study.
 prescription by dentists and antibiotic prophylaxis for tooth extraction: a Hypertension Research, pages 1123–1133, 2022.
 region-wide claims study in japan. Journal of Infection and Chemother- [43] H. Yamada, K. Goda, and M. Kitsuregawa. Nested Loops Revisited
 apy, 29(10):965–970, 2023. Again. In ICDE, pages 3708–3717, 2023.
 [22] M. Isard, M. Budiu, Y. Yu, A. Birrell, and D. Fetterly. Dryad: Distributed [44] M. Zaharia, M. Chowdhury, T. Das, A. Dave, J. Ma, M. McCauley, MJ.
 Data-parallel Programs from Sequential Building Blocks. In EuroSys, Franklin, S. Shenker, and I. Stoica. Resilient Distributed Datasets: A
 pages 59–72, 2007. Fault-tolerant Abstraction for In-memory Cluster Computing. In NSDI,
 [23] T. Ishikawa, J. Sato, J. Hattori, K. Goda, M. Kitsuregawa, and N. Mitsu- pages 15–28, 2012.
 take. The association between telehealth utilization and policy responses [45] M. Zaharia, A. Ghodsi, R. Xin, and M. Armbrust. Lakehouse: A
 on COVID-19 in japan: Interrupted time-series analysis. Interact. J. Med. New Generation of Open Platforms that Unify Data Warehousing and
 Res., 11(2):e39181, 2022. Advanced Analytics. In CIDR, 2021.
 [46] M. Ziane, M. Za¨ıt, and P. Borla-Salamet. Parallel Query Processing with
 Zigzag Trees. VLDBJ, 2(3):277–302, July 1993.

 5592

Authorized licensed use limited to: Trial User - National Taiwan University. Downloaded on May 14,2026 at 13:22:29 UTC from IEEE Xplore. Restrictions apply.