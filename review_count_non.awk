/^commit / {
	active = 1;
	cnt++;
	review_cnt = 0;
}

/^Author: / {
	author = substr($NF, index($NF, "@"));
}

/(Ack|Review)ed-by:/ {
	if (index($NF, author) == 0) {
		review_cnt++;
	}
}

/Signed-off-by: Jakub Kicinski|Signed-off-by: Paolo Abeni|Signed-off-by: David S. Miller/ {
	if (active) {
		sobs++;
		if (review_cnt) {
			reviewed++;
		}
	}
	active = 0;
}

END {
	print sobs, reviewed, reviewed * 100 / sobs;
}
