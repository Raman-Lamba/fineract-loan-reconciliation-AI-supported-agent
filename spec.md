The problem it solves: a lender gets hundreds of payments a day into its bank account. Each one has to be matched to the right borrower's loan and recorded in the loan system. The bank's descriptions are messy, so staff do this by hand in Excel. Until a payment is matched, the borrower looks overdue even though they paid.

One example
- Input: a bank line reading UPI/432198/RAMESH KUM/okaxis/emi sept, ₹8,450, 5 Sept.
- The agent finds Ramesh Kumar's loan with an ₹8,450 instalment due on 5 Sept and records the repayment.
- Output: the loan is no longer overdue, ₹6,760 is marked as owed to the partner bank and ₹1,690 to the lender, and the log shows why it matched.
- If two borrowers named Ramesh both owed ₹8,450, it would not guess. It would put the line in a queue for a person, with both candidates and its reasoning.

How it adds value
- Staff handle only the unclear cases, not every line.
- Borrowers' overdue status is right the same day, so nobody gets a wrong collections call.
- Less money sits unexplained in the holding account.
- The partner bank gets its correct share, with a report an auditor can check.
- Every action has a recorded reason and approver.