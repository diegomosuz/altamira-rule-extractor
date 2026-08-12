       IDENTIFICATION DIVISION.
       PROGRAM-ID. GTSQLCD1.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01 WS-CUENTA             PIC X(10).
       01 WS-ESTADO             PIC X(1).
       01 WS-ESTADO-OPERACION   PIC X(1) VALUE SPACES.
       PROCEDURE DIVISION.
       MAIN-PARA.
           EXEC SQL
               SELECT ESTADO
               INTO :WS-ESTADO
               FROM CUENTAS
               WHERE CUENTA = :WS-CUENTA
           END-EXEC.
           EVALUATE SQLCODE
               WHEN 0
                   MOVE 'A' TO WS-ESTADO-OPERACION
               WHEN +100
                   MOVE 'N' TO WS-ESTADO-OPERACION
               WHEN OTHER
                   MOVE 'E' TO WS-ESTADO-OPERACION
           END-EVALUATE.
