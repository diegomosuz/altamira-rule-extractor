       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTNEG001.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CONTADOR              PIC 9(4).
       PROCEDURE DIVISION.
       MAIN-PARA.
           IF WS-CONTADOR > 10
               MOVE 1 TO WS-CONTADOR
           ELSE
               MOVE 0 TO WS-CONTADOR
           END-IF.
