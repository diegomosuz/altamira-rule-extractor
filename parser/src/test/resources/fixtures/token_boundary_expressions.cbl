       IDENTIFICATION DIVISION.
       PROGRAM-ID. TOKENBND1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 SQLCODE              PIC S9(4) COMP VALUE 0.
       01 WS-FLAG               PIC X(1) VALUE 'N'.
           88 WS-FLAG-ACTIVO     VALUE 'S'.
       01 WS-REINTENTO-OK       PIC X(1) VALUE 'N'.
       01 A                     PIC 9(4) VALUE 0.
       01 B                     PIC 9(4) VALUE 0.
       01 C                     PIC 9(4) VALUE 0.
       01 D                     PIC 9(4) VALUE 0.
       01 WS-NOTA               PIC X(20) VALUE SPACES.
       PROCEDURE DIVISION.
       IF-SQLCODE-NOT-PARA.
           IF SQLCODE NOT = 0
               MOVE 1 TO A
           END-IF.
       IF-FLAG-NOT-PARA.
           IF WS-REINTENTO-OK NOT = 'S'
               MOVE 1 TO A
           END-IF.
       IF-EQ-PARA.
           IF A = 1
               MOVE 1 TO B
           END-IF.
       IF-GT-PARA.
           IF A > 1
               MOVE 1 TO B
           END-IF.
       IF-GE-PARA.
           IF A >= 1
               MOVE 1 TO B
           END-IF.
       IF-LT-PARA.
           IF A < 1
               MOVE 1 TO B
           END-IF.
       IF-LE-PARA.
           IF A <= 1
               MOVE 1 TO B
           END-IF.
       IF-AND-PARA.
           IF A > 0 AND B < 10
               MOVE 1 TO C
           END-IF.
       IF-OR-PARA.
           IF A = 1 OR B = 2
               MOVE 1 TO C
           END-IF.
       EVALUATE-BARE-PARA.
           EVALUATE SQLCODE
               WHEN 0
                   MOVE 1 TO A
               WHEN OTHER
                   MOVE 0 TO A
           END-EVALUATE.
       EVALUATE-PLUS100-PARA.
           EVALUATE SQLCODE
               WHEN +100
                   MOVE 1 TO A
               WHEN OTHER
                   MOVE 0 TO A
           END-EVALUATE.
       EVALUATE-TRUE-CONDNAME-PARA.
           EVALUATE TRUE
               WHEN WS-FLAG-ACTIVO
                   MOVE 1 TO A
               WHEN OTHER
                   MOVE 0 TO A
           END-EVALUATE.
       COMPUTE-MULTIPLY-PARA.
           COMPUTE A = B * C.
       COMPUTE-PAREN-PARA.
           COMPUTE A = ( B + C ) / D.
       IF-QUOTED-LITERAL-SPACE-PARA.
           IF WS-NOTA = 'HELLO WORLD'
               MOVE 1 TO A
           END-IF.
