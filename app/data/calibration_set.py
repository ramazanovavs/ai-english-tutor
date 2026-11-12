"""
Control set for the monthly check of AI scoring (drift after model updates).
Four IELTS Writing Task 2 answers to the same question, written to show different levels. Each run assesses
them with the same scorers the learners get (IELTS Writing and Writing Coach). The first run, or a run the
admin marks as the reference, is the baseline; later runs are compared with it.
The "intended" values are the levels the texts were written to show; teachers can replace them with their own
marks after checking the texts (see the calibration procedure document).
"""

PROMPT = ("Some people believe that university education should be free for everyone, while others think "
          "students should pay for it themselves. Discuss both views and give your own opinion.")

TEXTS = [
    {"id": "c1", "intended_band": 4.5, "intended_cefr": "A2",
     "text": "In my country many people want university free. I think it is good idea but also have problem. "
             "First, free university is good for poor family because they dont have money. Many student cannot "
             "study because it is very expensive and they must work. If university free, more people go and get "
             "good job. Second, some people say student must pay. Because goverment dont have many money and "
             "this money need for hospital and school. Also if student pay he study more hard because he pay "
             "money. In my opinion university must be free for poor people but rich people can pay. This is "
             "better for everybody. Goverment can help the student who is good in study. So I think both side "
             "have good idea but free for poor student is the best way for my country and for future."},
    {"id": "c2", "intended_band": 5.5, "intended_cefr": "B1",
     "text": "Nowadays the question of paying for university is discussed a lot. Some people think that higher "
             "education should be free for everyone, but others believe that students should pay for it. In this "
             "essay I will discuss both views and give my opinion.\n\nOn the one hand, free education gives equal "
             "chances to all young people. For example, a talented student from a village may not have enough "
             "money, so he cannot continue his studies. If education is free, the country will have more "
             "specialists like doctors and engineers.\n\nOn the other hand, education costs a lot of money for "
             "the government. Teachers, buildings and equipment are very expensive. Some people say that students "
             "who pay are more motivated and they don't miss lessons. Moreover, the money which government saves "
             "can be used for other important things, for example healthcare. "
             "Also some students choose a speciality without thinking and later they change it, so free places "
             "are wasted. If they pay, they will think more carefully before they choose. My cousin, for example, "
             "paid for his studies and he was always very serious about his exams.\n\nIn conclusion, I think that "
             "university education should be free for students who have good results, and other students should "
             "pay a part of the cost. In this way the government can support talented young people and save money. I believe this "
             "system is fair for students, for their parents and for the country, and it will help our economy "
             "to develop in the future."},
    {"id": "c3", "intended_band": 6.5, "intended_cefr": "B2",
     "text": "Whether higher education should be funded by the state or by students themselves is a controversial "
             "issue. While there are convincing arguments on both sides, I believe that a mixed approach is the "
             "most reasonable solution.\n\nThose who support free university education argue that it is a basic "
             "right which should not depend on family income. When tuition is free, gifted students from "
             "low-income families are able to develop their potential, which ultimately benefits the whole "
             "society. Countries such as Germany have shown that free tuition can coexist with a strong economy. "
             "Furthermore, a well-educated population tends to be healthier and more involved in public life, so "
             "the investment pays off in the long term.\n\n"
             "However, opponents point out that providing free education for everyone places a heavy burden on "
             "the state budget. Since graduates usually earn higher salaries, it seems fair that they contribute "
             "to the cost of their degree. In addition, paying fees may encourage students to take their studies "
             "more seriously and choose their courses more carefully.\n\nIn my opinion, the government should "
             "cover tuition for students who cannot afford it and for those who choose professions the country "
             "needs, such as teaching or medicine, whereas others could repay a loan after they start working. "
             "This would keep education accessible without making it a burden for taxpayers. Such a system "
             "already exists in several countries and, although it is not perfect, it shows that access and "
             "fairness do not have to be in conflict, provided that the repayment terms are reasonable."},
    {"id": "c4", "intended_band": 7.5, "intended_cefr": "C1",
     "text": "The funding of higher education has become a contentious issue as governments struggle to balance "
             "rising costs against the demand for an educated workforce. Although the case for universally free "
             "tuition is compelling, I would argue that a system combining public funding with income-contingent "
             "contributions is both fairer and more sustainable.\n\nAdvocates of free university education "
             "maintain that access to knowledge should not be determined by the size of a family's bank account. "
             "Removing tuition fees widens participation, particularly among first-generation students, and the "
             "resulting gains in productivity and civic engagement arguably repay the initial public investment. "
             "A society in which talent rather than wealth determines who studies is also likely to be more "
             "cohesive, since social mobility reduces the resentment that entrenched inequality tends to produce. "
             "Moreover, graduates who are not burdened by debt are freer to pursue socially valuable but poorly "
             "paid careers.\n\nNevertheless, the opposing view deserves serious consideration. Since a degree "
             "typically raises lifetime earnings, financing it entirely through general taxation means that "
             "non-graduates, who are often on lower incomes, subsidise those who will later be better off. "
             "Furthermore, when places are free, universities may be chronically underfunded, which inevitably "
             "affects the quality of teaching.\n\nOn balance, the most equitable solution is for the state to "
             "guarantee free access at the point of entry, while graduates repay part of the cost only once their "
             "income exceeds a certain threshold. Such a model preserves opportunity without asking the wider "
             "public to shoulder the entire burden, and it ties the cost of a degree to the benefits it "
             "actually brings."},
]
