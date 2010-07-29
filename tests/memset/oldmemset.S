	.global	old_android_memset16
	.type   old_android_memset16, @function
old_android_memset16:
        .ent	old_android_memset16

	.set	noreorder
	beqz	$a2,9f
	 srl	$a2,1

1:	sh	$a1,($a0)
	subu	$a2,1
	bnez	$a2,1b
	 addu	$a0,2
	.set reorder

9:	j	$ra
        .end	old_android_memset16
	.size	old_android_memset16,.-old_android_memset16

	.global old_android_memset32
	.type	old_android_memset32, @function
old_android_memset32:
        .ent	old_android_memset32
	.set	noreorder
	beqz	$a2,9f
	 srl	$a2,2

1:	sw	$a1,($a0)
	subu	$a2,1
	bnez	$a2,1b
	 addu	$a0,4
	.set reorder

9:	j	$ra
        .end	old_android_memset32
	.size	old_android_memset32,.-old_android_memset32
	
